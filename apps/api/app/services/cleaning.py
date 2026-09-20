from typing import Any

import numpy as np
import polars as pl

from apps.api.app.config import settings
from apps.api.app.schemas.mapping import MappingConfirmationRequest


def detect_date_format_ambiguity(df: pl.DataFrame, date_col: str) -> str | None:
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
    date_preference: str | None = None,
) -> pl.Series:
    """Parse a column into pl.Date with robust format matching."""
    if series.dtype in (pl.Date, pl.Datetime):
        return series.cast(pl.Date)

    # First try default polars string to date (ISO etc.)
    try:
        parsed = series.str.to_date(strict=False)
        if parsed.null_count() < len(series) * 0.1:
            return parsed
    except Exception:
        pass

    # Try specific formats based on preference
    formats = []
    if date_preference == "dayfirst":
        formats = ["%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y", "%Y-%m-%d", "%Y/%m/%d"]
    elif date_preference == "monthfirst":
        formats = ["%m-%d-%Y", "%m/%d/%Y", "%m-%d-%y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d"]
    else:
        # Standard search order
        formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%m-%d-%Y",
            "%m/%d/%Y",
            "%Y%m%d",
        ]

    for fmt in formats:
        try:
            parsed = series.str.to_date(fmt, strict=False)
            if parsed.null_count() < len(series) * 0.2:
                return parsed
        except Exception:
            continue

    # Fallback to general parsing with strict=False
    return series.cast(pl.Utf8).str.to_date(strict=False)


def detect_outliers_iqr(series: pl.Series, multiplier: float = 3.0) -> tuple[pl.Series, float]:
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


def clean_and_regularize_dataset(
    df: pl.DataFrame,
    mapping_config: MappingConfirmationRequest,
    dataset_id: str,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """
    Executes Phase 1 data cleaning:
    1. Renames source columns to logical names.
    2. Parses dates with ambiguity resolution.
    3. Handles duplicates according to user preference.
    4. Separates negative quantities (returns).
    5. Aggregates to regular grid (daily or weekly).
    6. Fills gaps with 0 ONLY between first and last sale of each product.
    7. Flags outliers (and optionally caps them).
    8. Writes Parquet to disk.
    """
    mapping = mapping_config.mapping
    date_col = mapping["date"]
    product_col = mapping["product"]
    qty_col = mapping["quantity"]
    store_col = mapping.get("store")
    promo_col = mapping.get("promo_flag")
    rev_col = mapping.get("revenue")

    raw_row_count = len(df)

    # 1. Column renaming
    rename_dict = {
        date_col: "date",
        product_col: "product",
        qty_col: "raw_quantity",
    }
    if store_col and store_col in df.columns:
        rename_dict[store_col] = "store"
    if promo_col and promo_col in df.columns:
        rename_dict[promo_col] = "promo_flag"
    if rev_col and rev_col in df.columns:
        rename_dict[rev_col] = "revenue"

    work_df = df.rename(rename_dict)

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
        # Truncate to Monday start
        work_df = work_df.with_columns(pl.col("date").dt.truncate("1w").alias("date"))

    # Group by key and date
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

    # 6. Regular grid expansion (zero-filling ONLY between first and last sale of each product)
    # Process per product group
    filled_dfs = []
    unique_products = grouped_df["product"].unique().to_list()
    total_outliers = 0

    for prod in unique_products:
        prod_df = grouped_df.filter(pl.col("product") == prod)
        if len(prod_df) == 0:
            continue

        min_date = prod_df["date"].min()
        max_date = prod_df["date"].max()

        # Generate complete date range from min_date to max_date
        interval = "1d" if mapping_config.aggregation_granularity == "daily" else "1w"
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

    cleaning_meta = {
        "raw_row_count": raw_row_count,
        "cleaned_row_count": len(cleaned_df),
        "duplicate_count": int(duplicate_count),
        "outlier_count": total_outliers,
        "parquet_path": str(parquet_path),
        "granularity": mapping_config.aggregation_granularity,
        "unique_products": len(unique_products),
    }

    return cleaned_df, cleaning_meta
