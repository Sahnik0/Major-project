from typing import Any, Dict, Optional, Tuple

import numpy as np
import polars as pl

from apps.api.app.config import settings
from apps.api.app.schemas.mapping import MappingConfirmationRequest


def detect_date_format_ambiguity(df: pl.DataFrame, date_col: str) -> Optional[str]:
    """
    Detect whether date column is predominantly dayfirst (DD-MM-YYYY) or monthfirst (MM-DD-YYYY).
    Returns 'dayfirst', 'monthfirst', 'iso', or 'ambiguous'.
    """
    sample = df[date_col].drop_nulls().head(200).to_list()
    has_day_gt_12 = False
    has_month_gt_12 = False

    for val in sample:
        s = str(val).strip()
        # Check ISO format YYYY-MM-DD
        if len(s) >= 10 and (s[4] in "-/" and s[7] in "-/"):
            return "iso"

        # Check DD/MM/YYYY or MM/DD/YYYY
        parts = []
        if "/" in s:
            parts = s.split("/")
        elif "-" in s:
            parts = s.split("-")

        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            first = int(parts[0])
            second = int(parts[1])
            if first > 12 >= second:
                has_day_gt_12 = True
            elif second > 12 >= first:
                has_month_gt_12 = True

    if has_day_gt_12 and not has_month_gt_12:
        return "dayfirst"
    elif has_month_gt_12 and not has_day_gt_12:
        return "monthfirst"
    return "ambiguous"


def parse_date_column(
    series: pl.Series,
    date_preference: Optional[str] = None,
) -> pl.Series:
    """
    Dynamically parses any time or date format into pl.Date without hardcoded format restrictions.
    Handles:
    - ISO 8601 strings and datetimes with/without timezones
    - Mixed date formats in the same series
    - Textual dates (e.g. '15-Jan-2023', 'Oct 12, 2023')
    - Excel serial numbers (e.g. 44927)
    - Unix epoch timestamps in seconds or milliseconds
    - Various separators (-, /, ., space)
    """
    if series.dtype in (pl.Date, pl.Datetime):
        return series.cast(pl.Date)

    import pandas as pd

    try:
        p_s = series.to_pandas()
        if pd.api.types.is_numeric_dtype(p_s):
            max_val = p_s.max()
            if max_val is not None and max_val > 1e11:
                dt = pd.to_datetime(p_s, unit="ms", errors="coerce")
            elif max_val is not None and max_val > 1e8:
                dt = pd.to_datetime(p_s, unit="s", errors="coerce")
            elif max_val is not None and 20000 <= max_val <= 80000:
                dt = pd.to_datetime(p_s, unit="D", origin="1899-12-30", errors="coerce")
            else:
                dt = pd.to_datetime(p_s.astype(str), errors="coerce", format="mixed")
        else:
            dayfirst = True if date_preference == "dayfirst" else False
            dt = pd.to_datetime(p_s, errors="coerce", format="mixed", dayfirst=dayfirst, utc=True)

        if hasattr(dt.dt, "tz") and dt.dt.tz is not None:
            dt = dt.dt.tz_localize(None)

        parsed_dates = dt.dt.date
        return pl.Series(series.name, parsed_dates).cast(pl.Date)
    except Exception:
        # Fallback to general polars parsing
        return series.cast(pl.Utf8).str.to_date(strict=False)


def infer_natural_frequency(series: pl.Series) -> Tuple[str, str]:
    """
    Dynamically infers the time granularity of historical dates without hardcoding.
    Returns:
        interval: Polars date_range interval string ('1d', '1w', '1mo', '1q', '1y')
        freq_code: Pandas/TimesFM frequency code ('D', 'W', 'M', 'Q', 'Y')
    """
    unique_dates = series.drop_nulls().unique().sort().to_list()
    if len(unique_dates) < 2:
        return "1d", "D"

    deltas = []
    for i in range(len(unique_dates) - 1):
        d1 = unique_dates[i]
        d2 = unique_dates[i + 1]
        diff_days = (d2 - d1).days
        if diff_days > 0:
            deltas.append(diff_days)

    if not deltas:
        return "1d", "D"

    median_delta = float(np.median(deltas))
    if median_delta <= 2:
        return "1d", "D"
    elif 5 <= median_delta <= 10:
        return "1w", "W"
    elif 25 <= median_delta <= 35:
        return "1mo", "M"
    elif 80 <= median_delta <= 100:
        return "1q", "Q"
    elif 350 <= median_delta <= 380:
        return "1y", "Y"
    else:
        return "1d", "D"


def detect_outliers_iqr(series: pl.Series, multiplier: float = 3.0) -> Tuple[pl.Series, float]:
    """
    Detect outliers using IQR on positive demand.
    Returns boolean series (True if outlier) and the upper threshold.
    """
    vals = series.to_numpy()
    positive = vals[vals > 0]
    if len(positive) < 5:
        return pl.Series([False] * len(series)), float("inf")

    q25, q75 = np.percentile(positive, [25, 75])
    iqr = q75 - q25
    upper_bound = q75 + multiplier * iqr
    if upper_bound <= 0 or np.isnan(upper_bound):
        upper_bound = float("inf")

    is_outlier = pl.Series((series > upper_bound).to_list())
    return is_outlier, float(upper_bound)


def _clean_and_regularize_stock_summary(
    df: pl.DataFrame,
    mapping_config: MappingConfirmationRequest,
    dataset_id: str,
) -> Tuple[pl.DataFrame, Dict[str, Any]]:
    """
    Cleans and regularizes an ERP Stock Summary / Inventory Snapshot dataset.
    Aggregates products, calculates on-hand stock and sales metrics,
    and synthesizes a regularized time series for foundation forecasting.
    """
    from datetime import date, timedelta
    mapping = mapping_config.mapping
    product_col = mapping.get("product") or "product"
    qty_col = mapping.get("quantity") or "quantity"
    stock_col = mapping.get("stock_on_hand")
    rev_col = mapping.get("revenue")

    raw_row_count = len(df)

    # Extract date range from mapping metadata if available
    date_range = mapping.get("_date_range")
    if date_range and isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        try:
            start_dt = date.fromisoformat(str(date_range[0]))
            end_dt = date.fromisoformat(str(date_range[1]))
        except Exception:
            end_dt = date.today()
            start_dt = end_dt - timedelta(days=180)
    else:
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=180)

    total_days = max(14, (end_dt - start_dt).days + 1)

    # Fill nulls and cast
    exprs = [
        pl.col(qty_col).fill_null(0.0).cast(pl.Float64).alias("qty"),
    ]
    if stock_col and stock_col in df.columns:
        exprs.append(pl.col(stock_col).fill_null(0.0).cast(pl.Float64).alias("stock"))
    else:
        exprs.append(pl.lit(0.0).alias("stock"))

    if rev_col and rev_col in df.columns:
        exprs.append(pl.col(rev_col).fill_null(0.0).cast(pl.Float64).alias("rev"))
    else:
        exprs.append(pl.lit(0.0).alias("rev"))

    work_df = df.with_columns(exprs)

    # Aggregate by product
    grouped = work_df.group_by(product_col).agg([
        pl.col("qty").sum().alias("total_units_sold"),
        pl.col("stock").sum().alias("current_stock"),
        pl.col("rev").sum().alias("total_revenue"),
    ]).filter(
        pl.col(product_col).is_not_null()
        & (pl.col(product_col).cast(pl.Utf8).str.strip_chars() != "")
        & (pl.col(product_col).cast(pl.Utf8).str.to_uppercase() != "NA")
    ).sort("total_units_sold", descending=True)

    unique_products_count = len(grouped)

    # Stock Health Categorization
    lead_time_days = 7
    z = 1.645
    out_of_stock = []
    critical = []
    healthy = []
    overstocked = []

    for row in grouped.to_dicts():
        p = str(row[product_col])
        sold = float(row["total_units_sold"])
        stock = float(row["current_stock"])
        daily_rate = sold / total_days
        rop = lead_time_days * daily_rate + z * np.sqrt(lead_time_days) * max(0.5, np.sqrt(max(0.0, daily_rate)))
        order_qty = max(0, int(np.ceil(rop - stock)))

        info = {
            "product": p,
            "sold": round(sold, 1),
            "stock": round(stock, 1),
            "rop": round(float(rop), 1),
            "order_qty": order_qty,
        }
        if stock <= 0 and sold > 0:
            out_of_stock.append(info)
        elif stock < rop:
            critical.append(info)
        elif daily_rate > 0 and stock > daily_rate * 90:
            overstocked.append(info)
        else:
            healthy.append(info)

    # Synthesize time-series for top 100 products for rolling backtesting & TimesFM
    top_items = grouped.head(100).to_dicts()
    date_list = [start_dt + timedelta(days=i) for i in range(total_days)]
    records = []
    for item in top_items:
        p_name = str(item[product_col])
        sold = float(item["total_units_sold"])
        stk = float(item["current_stock"])
        rev = float(item["total_revenue"])
        daily_rate = sold / total_days
        for dt in date_list:
            dow = dt.weekday()
            cycle = 1.25 if dow in (5, 6) else 0.90
            q = max(0.0, round(daily_rate * cycle, 2))
            records.append({
                "date": dt,
                "product": p_name,
                "quantity": q,
                "stock_on_hand": stk,
                "revenue": round(q * (rev / max(1.0, sold)), 2),
            })

    cleaned_df = pl.DataFrame(records).sort(["product", "date"])

    # Persist Parquet
    parquet_filename = f"{dataset_id}.parquet"
    parquet_path = settings.PARQUET_DIR / parquet_filename
    cleaned_df.write_parquet(parquet_path)

    # Value calculation — dynamically detect any column representing monetary stock value
    total_stock_units = float(work_df["stock"].sum())
    total_stock_value = 0.0
    import re as _re
    _value_kw = ("value", "val", "amount", "amt", "worth", "valuation")
    _exclude_kw = ("qty", "quantity", "rate_pct", "percent", "pct", "%", "sale", "sold")
    for v_col in df.columns:
        v_norm = _re.sub(r"[^a-z0-9]+", "_", v_col.strip().lower()).strip("_")
        has_value_kw = any(kw in v_norm for kw in _value_kw)
        has_exclude_kw = any(kw in v_norm for kw in _exclude_kw)
        if has_value_kw and not has_exclude_kw:
            try:
                total_stock_value = float(df[v_col].fill_null(0.0).cast(pl.Float64).sum())
                break
            except Exception:
                continue


    cleaning_meta = {
        "raw_row_count": raw_row_count,
        "cleaned_row_count": len(cleaned_df),
        "duplicate_count": 0,
        "outlier_count": 0,
        "parquet_path": str(parquet_path),
        "granularity": "daily",
        "frequency_code": "D",
        "unique_products": unique_products_count,
        "dataset_type": "stock_summary",
        "stock_health": {
            "out_of_stock": len(out_of_stock),
            "critical": len(critical),
            "healthy": len(healthy),
            "overstocked": len(overstocked),
        },
        "top_out_of_stock": out_of_stock[:8],
        "top_critical": critical[:8],
        "top_moving": [
            {"product": r["product"], "sold": r["sold"], "stock": r["stock"]}
            for r in (out_of_stock + critical + healthy + overstocked)[:8]
        ],
        "total_stock_units": total_stock_units,
        "total_stock_value": total_stock_value,
        "date_min": start_dt.isoformat(),
        "date_max": end_dt.isoformat(),
        "total_days": total_days,
    }

    return cleaned_df, cleaning_meta


def _clean_and_regularize_tabular(
    df: pl.DataFrame,
    mapping_config: MappingConfirmationRequest,
    dataset_id: str,
) -> Tuple[pl.DataFrame, Dict[str, Any]]:
    """
    Cleans and prepares a general cross-sectional / tabular business dataset
    (without forcing a synthetic date axis), preserving all columns for SQL/Polars analysis.
    """
    mapping = mapping_config.mapping
    product_col = mapping.get("product")
    qty_col = mapping.get("quantity")

    raw_row_count = len(df)
    duplicate_count = max(0, len(df) - len(df.unique()))
    work_df = df.unique() if (mapping_config.drop_duplicates and duplicate_count > 0) else df

    # Determine unique entities count and top entities
    unique_products_count = len(work_df)
    top_moving = []
    outlier_count = 0

    if product_col and product_col in work_df.columns:
        unique_products_count = int(work_df[product_col].drop_nulls().n_unique())
        if qty_col and qty_col in work_df.columns and work_df[qty_col].dtype in (
            pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64, pl.Float32, pl.Float64
        ):
            is_out, _ = detect_outliers_iqr(work_df[qty_col].fill_null(0.0).cast(pl.Float64))
            outlier_count = int(is_out.sum())
            grp = (
                work_df.filter(pl.col(product_col).is_not_null())
                .group_by(product_col)
                .agg(pl.col(qty_col).fill_null(0.0).cast(pl.Float64).sum().alias("metric_sum"))
                .sort("metric_sum", descending=True)
                .head(8)
            )
            top_moving = [
                {"product": str(r[product_col]), "sold": round(float(r["metric_sum"]), 2), "stock": 0}
                for r in grp.to_dicts()
            ]
        else:
            grp = (
                work_df.filter(pl.col(product_col).is_not_null())
                .group_by(product_col)
                .len()
                .sort("len", descending=True)
                .head(8)
            )
            top_moving = [
                {"product": str(r[product_col]), "sold": int(r["len"]), "stock": 0}
                for r in grp.to_dicts()
            ]

    parquet_filename = f"{dataset_id}.parquet"
    parquet_path = settings.PARQUET_DIR / parquet_filename
    work_df.write_parquet(parquet_path)

    cleaning_meta = {
        "raw_row_count": raw_row_count,
        "cleaned_row_count": len(work_df),
        "duplicate_count": int(duplicate_count),
        "outlier_count": outlier_count,
        "parquet_path": str(parquet_path),
        "granularity": "cross_sectional",
        "frequency_code": "NONE",
        "unique_products": unique_products_count,
        "dataset_type": "tabular",
        "top_moving": top_moving,
        "total_columns": len(work_df.columns),
        "row_grain": mapping.get("_row_grain"),
    }
    return work_df, cleaning_meta


def _is_non_additive_metric(col_name: str) -> bool:
    """Returns True if metric represents a rate, average, price, index, or percentage rather than additive volume."""
    import re as _re

    norm = _re.sub(r"[^a-z0-9]+", "_", col_name.strip().lower()).strip("_")
    tokens = set(norm.split("_"))
    additive_tokens = {"qty", "quantity", "units", "sold", "volume", "count", "revenue", "sales", "amount", "profit", "total"}
    if tokens & additive_tokens:
        return False
    rate_tokens = {"ma", "avg", "average", "mean", "median", "price", "rate", "pct", "percent", "index", "ratio", "margin", "score", "close"}
    return bool(tokens & rate_tokens) or "%" in col_name


def clean_and_regularize_dataset(
    df: pl.DataFrame,
    mapping_config: MappingConfirmationRequest,
    dataset_id: str,
) -> Tuple[pl.DataFrame, Dict[str, Any]]:
    """
    Executes universal data cleaning and regularization:
    - Stock Summary mode for ERP inventory snapshots
    - Tabular mode for cross-sectional datasets without dates
    - Time Series mode (daily, weekly, monthly, quarterly, yearly; single-series or multi-series)
    """
    mapping = mapping_config.mapping
    date_col = mapping.get("date", "date")
    if date_col == "__period__" or mapping.get("_dataset_type") == "stock_summary":
        return _clean_and_regularize_stock_summary(df, mapping_config, dataset_id)
    if date_col == "__tabular__" or mapping.get("_dataset_type") == "tabular" or date_col not in df.columns:
        return _clean_and_regularize_tabular(df, mapping_config, dataset_id)

    product_col = mapping.get("product", "__overall__")
    qty_col = mapping["quantity"]
    store_col = mapping.get("store")
    promo_col = mapping.get("promo_flag")
    rev_col = mapping.get("revenue")
    composite_cols = mapping.get("_composite_product_cols")

    raw_row_count = len(df)
    working_source = df

    # Synthesize product column if single-series or composite-grain time series
    if composite_cols and isinstance(composite_cols, list) and all(c in working_source.columns for c in composite_cols):
        c1, c2 = composite_cols[0], composite_cols[1]
        working_source = working_source.with_columns(
            (
                pl.col(c1).cast(pl.Utf8).fill_null("Unknown")
                + pl.lit(f" ({c2}=")
                + pl.col(c2).cast(pl.Utf8).fill_null("?")
                + pl.lit(")")
            ).alias("__synth_product__")
        )
        effective_prod_col = "__synth_product__"
    elif product_col == "__overall__" or product_col not in working_source.columns:
        working_source = working_source.with_columns(pl.lit("Overall").alias("__synth_product__"))
        effective_prod_col = "__synth_product__"
    else:
        effective_prod_col = product_col

    # 1. Column renaming
    rename_dict = {
        date_col: "date",
        effective_prod_col: "product",
        qty_col: "raw_quantity",
    }
    if store_col and store_col in working_source.columns and store_col not in rename_dict:
        rename_dict[store_col] = "store"
    if promo_col and promo_col in working_source.columns and promo_col not in rename_dict:
        rename_dict[promo_col] = "promo_flag"
    if rev_col and rev_col in working_source.columns and rev_col not in rename_dict:
        rename_dict[rev_col] = "revenue"

    work_df = working_source.rename(rename_dict)

    # Ensure required columns are present
    selected_cols = ["date", "product", "raw_quantity"]
    for opt in ["store", "promo_flag", "revenue"]:
        if opt in work_df.columns:
            selected_cols.append(opt)
    work_df = work_df.select(selected_cols)

    # 2. Check duplicates (number of redundant duplicate rows)
    duplicate_count = len(work_df) - len(work_df.unique())
    if mapping_config.drop_duplicates and duplicate_count > 0:
        work_df = work_df.unique()

    # 3. Robust Date Parsing
    date_pref = mapping_config.date_format
    if not date_pref:
        date_pref = detect_date_format_ambiguity(work_df, "date")
    work_df = work_df.with_columns(parse_date_column(work_df["date"], date_pref).alias("date"))

    # Drop rows where date, product, or quantity is null
    work_df = work_df.filter(
        pl.col("date").is_not_null()
        & pl.col("product").is_not_null()
        & pl.col("raw_quantity").is_not_null()
    )

    # Cast product to string and raw_quantity to float
    work_df = work_df.with_columns(
        pl.col("product").cast(pl.Utf8),
        pl.col("raw_quantity").cast(pl.Float64),
    )

    # 4. Handle negative returns
    work_df = work_df.with_columns(
        pl.when(pl.col("raw_quantity") >= 0)
        .then(pl.col("raw_quantity"))
        .otherwise(0.0)
        .alias("gross_quantity"),
        pl.when(pl.col("raw_quantity") < 0)
        .then(-pl.col("raw_quantity"))
        .otherwise(0.0)
        .alias("returns_quantity"),
    )

    # Effective quantity
    if mapping_config.returns_handling == "net":
        work_df = work_df.with_columns(pl.col("raw_quantity").alias("quantity"))
    else:
        work_df = work_df.with_columns(pl.col("gross_quantity").alias("quantity"))

    # 5. Grouping keys
    group_keys = ["product"]
    if "store" in work_df.columns and not mapping_config.aggregate_stores:
        group_keys.append("store")

    # Granularity adjustment
    if mapping_config.aggregation_granularity == "weekly":
        work_df = work_df.with_columns(pl.col("date").dt.truncate("1w").alias("date"))

    use_mean_agg = _is_non_additive_metric(qty_col)
    if use_mean_agg:
        agg_exprs = [
            pl.col("quantity").mean().alias("quantity"),
            pl.col("gross_quantity").mean().alias("gross_quantity"),
            pl.col("returns_quantity").mean().alias("returns_quantity"),
        ]
    else:
        agg_exprs = [
            pl.col("quantity").sum().alias("quantity"),
            pl.col("gross_quantity").sum().alias("gross_quantity"),
            pl.col("returns_quantity").sum().alias("returns_quantity"),
        ]
    if "revenue" in work_df.columns:
        agg_exprs.append(pl.col("revenue").cast(pl.Float64).sum().alias("revenue"))
    if "promo_flag" in work_df.columns:
        agg_exprs.append(pl.col("promo_flag").cast(pl.Int32).max().alias("promo_flag"))

    grouped_df = work_df.group_by(group_keys + ["date"]).agg(agg_exprs).sort(group_keys + ["date"])

    # 6. Regular grid expansion (zero-filling ONLY between first and last observation of each product)
    inferred_interval, inferred_freq = infer_natural_frequency(grouped_df["date"]) if len(grouped_df) > 0 else ("1d", "D")
    n_unique_dates = int(grouped_df["date"].n_unique()) if len(grouped_df) > 0 else 0

    if mapping_config.aggregation_granularity == "weekly":
        interval = "1w"
        inferred_freq = "W"
    elif inferred_freq in ("M", "Q", "Y") and n_unique_dates >= 4:
        # Preserve monthly / quarterly / yearly cadence instead of exploding into fake daily zeros
        interval = inferred_interval
    elif inferred_freq == "W" and n_unique_dates >= 6 and mapping_config.aggregation_granularity != "daily":
        interval = "1w"
    else:
        interval = "1d"
        inferred_freq = "D"

    all_observed_dates = grouped_df["date"].unique().sort() if len(grouped_df) > 0 else pl.Series("date", [], dtype=pl.Date)

    filled_dfs = []
    unique_products = grouped_df["product"].unique().to_list()
    total_outliers = 0

    for prod in unique_products:
        prod_df = grouped_df.filter(pl.col("product") == prod)
        if len(prod_df) == 0:
            continue

        min_date = prod_df["date"].min()
        max_date = prod_df["date"].max()

        if interval in ("1mo", "1q", "1y"):
            # Align to observed monthly/quarterly/yearly period timestamps so month-end dates (30th vs 31st) match cleanly
            full_dates = all_observed_dates.filter(
                (all_observed_dates >= min_date) & (all_observed_dates <= max_date)
            ).alias("date")
        else:
            full_dates = pl.date_range(
                start=min_date,
                end=max_date,
                interval=interval,
                eager=True,
            ).alias("date")

        grid_df = pl.DataFrame({"date": full_dates, "product": [prod] * len(full_dates)})

        # Left join with actual observed records
        merged = grid_df.join(prod_df, on=["date", "product"], how="left")

        # Zero-fill quantities
        merged = merged.with_columns(
            pl.col("quantity").fill_null(0.0),
            pl.col("gross_quantity").fill_null(0.0),
            pl.col("returns_quantity").fill_null(0.0),
        )
        if "revenue" in merged.columns:
            merged = merged.with_columns(pl.col("revenue").fill_null(0.0))
        if "promo_flag" in merged.columns:
            merged = merged.with_columns(pl.col("promo_flag").fill_null(0))

        # 7. Outlier Detection & Optional Capping
        is_outlier, upper_thresh = detect_outliers_iqr(merged["quantity"])
        merged = merged.with_columns(is_outlier.alias("is_outlier"))
        outlier_count = int(is_outlier.sum())
        total_outliers += outlier_count

        if mapping_config.cap_outliers and upper_thresh < float("inf"):
            merged = merged.with_columns(
                pl.when(pl.col("quantity") > upper_thresh)
                .then(upper_thresh)
                .otherwise(pl.col("quantity"))
                .alias("quantity")
            )

        filled_dfs.append(merged)

    if filled_dfs:
        cleaned_df = pl.concat(filled_dfs).sort(["product", "date"])
    else:
        cleaned_df = grouped_df

    # 8. Persist Parquet
    parquet_filename = f"{dataset_id}.parquet"
    parquet_path = settings.PARQUET_DIR / parquet_filename
    cleaned_df.write_parquet(parquet_path)

    granularity_map = {
        "1d": "daily",
        "1w": "weekly",
        "1mo": "monthly",
        "1q": "quarterly",
        "1y": "yearly",
    }

    cleaning_meta = {
        "raw_row_count": raw_row_count,
        "cleaned_row_count": len(cleaned_df),
        "duplicate_count": int(duplicate_count),
        "outlier_count": total_outliers,
        "parquet_path": str(parquet_path),
        "granularity": granularity_map.get(interval, "daily"),
        "frequency_code": inferred_freq,
        "unique_products": len(unique_products),
        "dataset_type": "time_series",
        "total_columns": len(df.columns),
        "row_grain": mapping.get("_row_grain"),
    }

    return cleaned_df, cleaning_meta
