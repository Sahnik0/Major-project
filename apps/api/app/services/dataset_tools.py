import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
import polars as pl
from sqlalchemy.orm import Session

from apps.api.app.config import settings
from apps.api.app.db import Dataset, ProductForecast
from apps.api.app.services.ingestion import read_raw_dataframe

logger = logging.getLogger("advisor.tools")

# In-memory LRU-like cache for dataset DataFrames: {dataset_id: (timestamp, df)}
_DF_CACHE: Dict[str, Tuple[float, pl.DataFrame]] = {}
_CACHE_TTL_SECONDS = 600  # 10 minutes


def get_dataset_dataframe(dataset: Dataset) -> pl.DataFrame:
    """Retrieves the raw Polars DataFrame for a dataset, using in-memory caching and robust path discovery."""
    now = time.time()
    if dataset.id in _DF_CACHE:
        ts, cached_df = _DF_CACHE[dataset.id]
        if now - ts < _CACHE_TTL_SECONDS:
            return cached_df

    target_path = None
    if dataset.file_path and Path(dataset.file_path).exists():
        target_path = Path(dataset.file_path)
    else:
        # Search in uploads folder by dataset id
        uploads_dir = settings.UPLOAD_DIR
        matching_uploads = list(uploads_dir.glob(f"{dataset.id}*"))
        if matching_uploads:
            target_path = matching_uploads[0]
        elif dataset.parquet_path and Path(dataset.parquet_path).exists():
            df = pl.read_parquet(Path(dataset.parquet_path))
            _DF_CACHE[dataset.id] = (now, df)
            return df
        else:
            # Check project root for matching filename
            root_dir = settings.BASE_DIR
            if dataset.name:
                cand = root_dir / dataset.name
                if cand.exists():
                    target_path = cand
                else:
                    cand_xlsx = root_dir / f"{dataset.name}.xlsx"
                    if cand_xlsx.exists():
                        target_path = cand_xlsx
            # Also check if any xlsx in root matches vwstocksum
            if not target_path:
                root_files = list(root_dir.glob("vwstocksum*.xlsx"))
                if root_files:
                    target_path = root_files[0]

    if not target_path or not target_path.exists():
        raise FileNotFoundError(f"No valid data file found for dataset {dataset.id}")

    df = read_raw_dataframe(target_path)

    # Evict oldest if cache exceeds 10 items
    if len(_DF_CACHE) >= 10:
        oldest_id = min(_DF_CACHE.keys(), key=lambda k: _DF_CACHE[k][0])
        del _DF_CACHE[oldest_id]

    _DF_CACHE[dataset.id] = (now, df)
    return df


def normalize_col_token(token: str) -> str:
    """Normalizes string to lowercase alphanumeric underscore."""
    return re.sub(r"[^a-z0-9]+", "_", token.strip().lower()).strip("_")


def resolve_column_name(
    available_cols: List[str],
    target: str,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Intelligently maps an informal or requested column name (e.g. 'profit', 'sales', 'stock')
    to the exact column string present in the dataset.
    """
    if not target:
        return None

    # 1. Exact match
    if target in available_cols:
        return target

    target_norm = normalize_col_token(target)

    # 2. Normalized exact match
    norm_map = {normalize_col_token(c): c for c in available_cols}
    if target_norm in norm_map:
        return norm_map[target_norm]

    # 3. Known domain synonyms
    synonyms = {
        "profit": ["profit_amt", "profit", "net_profit", "gross_profit", "profit_pct"],
        "margin": ["profit_pct", "profit_margin", "margin", "profit_amt"],
        "sales": ["net_sale_qty", "sale_qty", "sales_qty", "units_sold", "quantity", "sold_qty"],
        "quantity": ["net_sale_qty", "sale_qty", "sales_qty", "units_sold", "quantity", "qty"],
        "units": ["net_sale_qty", "sale_qty", "sales_qty", "quantity"],
        "sold": ["net_sale_qty", "sale_qty", "sales_qty", "units_sold"],
        "revenue": ["gross_item_sale", "gross_sales", "sale_value", "revenue", "net_sales", "turnover"],
        "turnover": ["gross_item_sale", "gross_sales", "revenue", "turnover"],
        "stock": ["closing_qty", "closing_stock", "closing", "stock_on_hand", "soh", "current_stock"],
        "closing": ["closing_qty", "closing_stock", "closing"],
        "opening": ["opening_qty", "opening", "op_qty"],
        "inward": ["net_inward_qty", "inward", "receipt"],
        "outward": ["net_outward_qty", "outward", "transfer"],
        "returns": ["sale_rtn_qty", "return_qty", "returned"],
        "discount": ["discount_amt", "discount_amount", "discount", "disc"],
        "cost": ["cost_price", "cl_value_in_rate", "cogs", "cost", "purchase_cost"],
        "brand": ["brand", "manufacturer", "label"],
        "product": ["product", "department", "sku", "item", "article", "style"],
        "category": ["main_category", "category", "section", "division", "department"],
        "department": ["department", "section", "category"],
        "vendor": ["vendor", "supplier", "party", "dealer"],
        "offtake": ["offtake_by_qty", "offtake_pct", "sell_through_rate", "sell_through"],
    }

    # If target matches a synonym group, search in priority order
    for syn_key, candidates in synonyms.items():
        if syn_key == target_norm or syn_key in target_norm or target_norm in syn_key:
            for cand in candidates:
                for norm_c, orig_c in norm_map.items():
                    if cand in norm_c:
                        return orig_c

    # 4. Substring containment match
    for norm_c, orig_c in norm_map.items():
        if target_norm in norm_c or norm_c in target_norm:
            return orig_c

    # 5. Check in col_semantics definitions
    if col_semantics:
        for orig_c, desc in col_semantics.items():
            if target_norm in normalize_col_token(desc):
                if orig_c in available_cols:
                    return orig_c

    return None


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DEFINITIONS (Exposed to Gemma AI Agent)
# ─────────────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "rank_top_bottom",
        "description": "Ranks and returns the top or bottom N entities (e.g. Brands, Departments, Products, Vendors) sorted by any metric such as Profit, Sales Volume, Stock on Hand, Revenue, or Offtake %.",
        "parameters": {
            "dimension": "Categorical column to group/rank by (e.g. 'BRAND', 'DEPARTMENT', 'Vendor', 'product')",
            "metric": "Numeric column to sort by (e.g. 'Profit Amt', 'Sale Qty', 'Closing Qty', 'Gross Item Sale')",
            "n": "Number of results to return (integer, default 5, max 15)",
            "ascending": "Boolean: false for top/highest (default), true for bottom/lowest",
        },
    },
    {
        "name": "filter_and_aggregate",
        "description": "Calculates total sum, average, min, max, or count of any numeric column, with optional filtering by another column (e.g. total profit where DEPARTMENT == 'SHIRT').",
        "parameters": {
            "target_metric": "The numeric column to calculate (e.g. 'Profit Amt', 'Sale Qty', 'Revenue', 'Closing Qty')",
            "agg_func": "Aggregation function: 'sum', 'mean', 'min', 'max', or 'count' (default 'sum')",
            "filter_column": "Optional column to filter on (e.g. 'BRAND', 'DEPARTMENT', 'Vendor')",
            "filter_value": "Optional text or value to filter for (e.g. 'PETER ENGLAND', 'JEANS')",
            "group_by": "Optional categorical column to break down results by",
        },
    },
    {
        "name": "column_distribution_stats",
        "description": "Computes exact statistical summary for any column: total sum, mean, median, standard deviation, min, max, 25th & 75th percentiles, and count of non-zero entries.",
        "parameters": {
            "column": "The column name to analyze (numeric or categorical)",
        },
    },
    {
        "name": "compare_groups",
        "description": "Performs side-by-side comparison between 2 or more entities (e.g. compare Brand A vs Brand B, or Department X vs Department Y) across sales, stock, and profit.",
        "parameters": {
            "dimension": "The categorical column (e.g. 'BRAND', 'Vendor', 'DEPARTMENT')",
            "entities": "List of 2 to 5 entity names to compare (e.g. ['BRAND_A', 'BRAND_B'])",
        },
    },
    {
        "name": "inspect_raw_sample",
        "description": "Searches and retrieves exact raw spreadsheet rows matching a search keyword or filter condition, showing real values across columns.",
        "parameters": {
            "search_column": "The column to search in (e.g. 'BRAND', 'DEPARTMENT', 'CAT1', 'Vendor')",
            "keyword": "The keyword to search for",
            "limit": "Max rows to return (default 5, max 10)",
        },
    },
    {
        "name": "inventory_status_lookup",
        "description": "Looks up real-time inventory decisions, lead-time reorder points, recommended order quantities, days of cover, and stockout risk for products.",
        "parameters": {
            "product_query": "Optional specific product or brand name to inspect",
            "risk_filter": "Optional risk level: 'high', 'critical', 'out_of_stock', 'medium', or 'low'",
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# TOOL IMPLEMENTATIONS (Deterministic Polars & Math)
# ─────────────────────────────────────────────────────────────────────────────

def execute_rank_top_bottom(
    df: pl.DataFrame,
    dimension: str,
    metric: str,
    n: int = 5,
    ascending: bool = False,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Ranks entities by metric."""
    dim_col = resolve_column_name(df.columns, dimension, col_semantics) or df.columns[0]
    metric_col = resolve_column_name(df.columns, metric, col_semantics)

    if not metric_col:
        # Fallback to first numeric column
        for c in df.columns:
            if df[c].dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float32, pl.Float64):
                metric_col = c
                break
    if not metric_col:
        return {"error": f"Could not find numeric metric column matching '{metric}'"}

    n = max(1, min(int(n or 5), 15))

    # Clean and aggregate
    work = df.filter(
        pl.col(dim_col).is_not_null()
        & (pl.col(dim_col).cast(pl.Utf8).str.strip_chars() != "")
        & (pl.col(dim_col).cast(pl.Utf8).str.to_uppercase() != "NA")
    )

    grouped = (
        work.group_by(dim_col)
        .agg([
            pl.col(metric_col).fill_null(0.0).cast(pl.Float64).sum().alias("metric_value"),
            pl.len().alias("record_count"),
        ])
        .sort("metric_value", descending=not ascending)
        .head(n)
    )

    results = []
    chart_data = []
    table_rows = []

    for r in grouped.to_dicts():
        dim_val = str(r[dim_col])
        val = round(float(r["metric_value"]), 2)
        count = int(r["record_count"])
        results.append({
            "entity": dim_val,
            "value": val,
            "records": count,
        })
        chart_data.append({
            "name": dim_val,
            "value": val,
        })
        table_rows.append([dim_val, f"{val:,.2f}", str(count)])

    direction = "Lowest (Bottom)" if ascending else "Highest (Top)"
    title = f"{direction} {len(results)} {dim_col} by {metric_col}"

    facts = {
        "ranking_direction": "bottom" if ascending else "top",
        "dimension": dim_col,
        "metric": metric_col,
        "count": len(results),
        "results": results,
    }

    return {
        "tool_name": "rank_top_bottom",
        "title": title,
        "dimension_column": dim_col,
        "metric_column": metric_col,
        "table_headers": [dim_col, metric_col, "Records"],
        "table_rows": table_rows,
        "chart_data": chart_data,
        "facts": facts,
    }


def execute_filter_and_aggregate(
    df: pl.DataFrame,
    target_metric: str,
    agg_func: str = "sum",
    filter_column: Optional[str] = None,
    filter_value: Optional[str] = None,
    group_by: Optional[str] = None,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Filters data and computes aggregate metrics."""
    metric_col = resolve_column_name(df.columns, target_metric, col_semantics)
    if not metric_col:
        for c in df.columns:
            if df[c].dtype in (pl.Float32, pl.Float64, pl.Int32, pl.Int64):
                metric_col = c
                break
    if not metric_col:
        return {"error": f"Could not find numeric column matching '{target_metric}'"}

    filtered_df = df
    applied_filter_desc = "All records"

    if filter_column and filter_value:
        f_col = resolve_column_name(df.columns, filter_column, col_semantics)
        if f_col:
            # Case-insensitive substring match
            filtered_df = df.filter(
                pl.col(f_col).cast(pl.Utf8).str.to_lowercase().str.contains(str(filter_value).lower())
            )
            applied_filter_desc = f"{f_col} matching '{filter_value}'"

    agg_func_clean = str(agg_func).lower().strip()
    if agg_func_clean not in ("sum", "mean", "min", "max", "count", "average", "avg"):
        agg_func_clean = "sum"
    if agg_func_clean in ("average", "avg"):
        agg_func_clean = "mean"

    # With Group By
    if group_by:
        grp_col = resolve_column_name(df.columns, group_by, col_semantics)
        if grp_col:
            agg_expr = (
                pl.col(metric_col).fill_null(0.0).cast(pl.Float64).sum().alias("val")
                if agg_func_clean == "sum"
                else (
                    pl.col(metric_col).fill_null(0.0).cast(pl.Float64).mean().alias("val")
                    if agg_func_clean == "mean"
                    else (
                        pl.col(metric_col).fill_null(0.0).cast(pl.Float64).max().alias("val")
                        if agg_func_clean == "max"
                        else pl.col(metric_col).fill_null(0.0).cast(pl.Float64).min().alias("val")
                    )
                )
            )
            grp_res = (
                filtered_df.filter(pl.col(grp_col).is_not_null())
                .group_by(grp_col)
                .agg(agg_expr)
                .sort("val", descending=True)
                .head(10)
            )
            rows = [[str(r[grp_col]), f"{round(float(r['val']), 2):,}" ] for r in grp_res.to_dicts()]
            chart_data = [{"name": str(r[grp_col]), "value": round(float(r["val"]), 2)} for r in grp_res.to_dicts()]
            facts = {
                "metric": metric_col,
                "operation": agg_func_clean,
                "filter": applied_filter_desc,
                "group_by": grp_col,
                "items": [{"group": r[0], "value": r[1]} for r in rows],
            }
            return {
                "tool_name": "filter_and_aggregate",
                "title": f"{agg_func_clean.upper()} of {metric_col} by {grp_col} ({applied_filter_desc})",
                "table_headers": [grp_col, f"{agg_func_clean.capitalize()} {metric_col}"],
                "table_rows": rows,
                "chart_data": chart_data,
                "facts": facts,
            }

    # Scalar Aggregate
    metric_series = filtered_df[metric_col].drop_nulls().cast(pl.Float64)
    total_val = float(metric_series.sum()) if len(metric_series) > 0 else 0.0
    mean_val = float(metric_series.mean()) if len(metric_series) > 0 else 0.0
    min_val = float(metric_series.min()) if len(metric_series) > 0 else 0.0
    max_val = float(metric_series.max()) if len(metric_series) > 0 else 0.0
    count_val = len(metric_series)

    primary_val = total_val if agg_func_clean == "sum" else (mean_val if agg_func_clean == "mean" else max_val)

    facts = {
        "metric": metric_col,
        "operation": agg_func_clean,
        "filter": applied_filter_desc,
        "result_value": round(primary_val, 2),
        "total_sum": round(total_val, 2),
        "average": round(mean_val, 2),
        "minimum": round(min_val, 2),
        "maximum": round(max_val, 2),
        "records_count": count_val,
    }

    table_rows = [
        ["Total Sum", f"{total_val:,.2f}"],
        ["Average (Mean)", f"{mean_val:,.2f}"],
        ["Minimum", f"{min_val:,.2f}"],
        ["Maximum", f"{max_val:,.2f}"],
        ["Records Filtered", f"{count_val:,}"],
    ]

    return {
        "tool_name": "filter_and_aggregate",
        "title": f"{agg_func_clean.upper()} of {metric_col} ({applied_filter_desc})",
        "table_headers": ["Metric Statistic", "Value"],
        "table_rows": table_rows,
        "facts": facts,
    }


def execute_column_distribution_stats(
    df: pl.DataFrame,
    column: str,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Calculates comprehensive summary distribution for a column."""
    col_name = resolve_column_name(df.columns, column, col_semantics)
    if not col_name:
        return {"error": f"Column '{column}' was not found in the dataset"}

    series = df[col_name]
    is_numeric = series.dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float32, pl.Float64)

    if is_numeric:
        s_clean = series.drop_nulls().cast(pl.Float64)
        vals = s_clean.to_numpy()
        total_sum = float(np.sum(vals)) if len(vals) > 0 else 0.0
        mean = float(np.mean(vals)) if len(vals) > 0 else 0.0
        median = float(np.median(vals)) if len(vals) > 0 else 0.0
        std = float(np.std(vals)) if len(vals) > 0 else 0.0
        p25 = float(np.percentile(vals, 25)) if len(vals) > 0 else 0.0
        p75 = float(np.percentile(vals, 75)) if len(vals) > 0 else 0.0
        min_v = float(np.min(vals)) if len(vals) > 0 else 0.0
        max_v = float(np.max(vals)) if len(vals) > 0 else 0.0
        non_zero = int(np.sum(vals > 0))

        facts = {
            "column": col_name,
            "type": "numeric",
            "total_rows": len(df),
            "sum": round(total_sum, 2),
            "mean": round(mean, 2),
            "median": round(median, 2),
            "standard_deviation": round(std, 2),
            "p25": round(p25, 2),
            "p75": round(p75, 2),
            "minimum": round(min_v, 2),
            "maximum": round(max_v, 2),
            "non_zero_records": non_zero,
        }

        table_rows = [
            ["Sum", f"{total_sum:,.2f}"],
            ["Average (Mean)", f"{mean:,.2f}"],
            ["Median", f"{median:,.2f}"],
            ["Std Deviation", f"{std:,.2f}"],
            ["25th Percentile", f"{p25:,.2f}"],
            ["75th Percentile", f"{p75:,.2f}"],
            ["Min / Max Range", f"{min_v:,.2f} – {max_v:,.2f}"],
            ["Non-Zero Records", f"{non_zero:,} / {len(df):,}"],
        ]
    else:
        top_cats = (
            df.filter(pl.col(col_name).is_not_null())
            .group_by(col_name)
            .len()
            .sort("len", descending=True)
            .head(6)
        )
        unique_cnt = int(series.n_unique())
        top_list = [{"name": str(r[col_name]), "count": int(r["len"])} for r in top_cats.to_dicts()]
        facts = {
            "column": col_name,
            "type": "categorical",
            "unique_values_count": unique_cnt,
            "top_categories": top_list,
        }
        table_rows = [[r["name"], f"{r['count']:,}"] for r in top_list]

    return {
        "tool_name": "column_distribution_stats",
        "title": f"Distribution & Summary for '{col_name}'",
        "column": col_name,
        "table_headers": ["Statistic / Category", "Value"],
        "table_rows": table_rows,
        "facts": facts,
    }


def execute_compare_groups(
    df: pl.DataFrame,
    dimension: str,
    entities: List[str],
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Compares multiple entities across key metrics."""
    dim_col = resolve_column_name(df.columns, dimension, col_semantics) or df.columns[0]
    sale_col = resolve_column_name(df.columns, "sales", col_semantics)
    stock_col = resolve_column_name(df.columns, "stock", col_semantics)
    profit_col = resolve_column_name(df.columns, "profit", col_semantics)
    rev_col = resolve_column_name(df.columns, "revenue", col_semantics)

    if not entities or not isinstance(entities, list):
        entities = []

    # Filter for entities
    patterns = [str(e).strip().lower() for e in entities if str(e).strip()]
    if not patterns:
        top_ent = df[dim_col].drop_nulls().unique().head(3).to_list()
        patterns = [str(x).lower() for x in top_ent]

    comparison_results = []
    table_rows = []
    chart_data = []

    for ent in patterns:
        sub_df = df.filter(pl.col(dim_col).cast(pl.Utf8).str.to_lowercase().str.contains(ent))
        actual_name = (
            str(sub_df[dim_col].drop_nulls().head(1)[0]) if len(sub_df) > 0 else ent.upper()
        )

        sold = round(float(sub_df[sale_col].fill_null(0.0).sum()), 1) if sale_col and len(sub_df) > 0 else 0.0
        stock = round(float(sub_df[stock_col].fill_null(0.0).sum()), 1) if stock_col and len(sub_df) > 0 else 0.0
        profit = round(float(sub_df[profit_col].fill_null(0.0).sum()), 2) if profit_col and len(sub_df) > 0 else 0.0
        rev = round(float(sub_df[rev_col].fill_null(0.0).sum()), 2) if rev_col and len(sub_df) > 0 else 0.0

        item_facts = {
            "entity": actual_name,
            "units_sold": sold,
            "stock_on_hand": stock,
            "total_profit": profit,
            "total_revenue": rev,
            "records": len(sub_df),
        }
        comparison_results.append(item_facts)
        chart_data.append({"name": actual_name, "sold": sold, "stock": stock})
        table_rows.append([
            actual_name,
            f"{sold:,.0f}",
            f"{stock:,.0f}",
            f"{profit:,.2f}" if profit_col else "-",
            f"{rev:,.2f}" if rev_col else "-",
        ])

    headers = [dim_col, "Units Sold", "Stock on Hand"]
    if profit_col:
        headers.append("Profit")
    if rev_col:
        headers.append("Revenue")

    facts = {
        "dimension": dim_col,
        "compared_entities": comparison_results,
    }

    return {
        "tool_name": "compare_groups",
        "title": f"Comparison across {dim_col}",
        "table_headers": headers,
        "table_rows": table_rows,
        "chart_data": chart_data,
        "facts": facts,
    }


def execute_inspect_raw_sample(
    df: pl.DataFrame,
    search_column: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 5,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Retrieves exact sample rows matching search criteria."""
    limit = max(1, min(int(limit or 5), 10))

    if search_column and keyword:
        col_name = resolve_column_name(df.columns, search_column, col_semantics) or search_column
        if col_name in df.columns:
            sub_df = df.filter(
                pl.col(col_name).cast(pl.Utf8).str.to_lowercase().str.contains(str(keyword).lower())
            )
        else:
            sub_df = df
    else:
        sub_df = df

    sample = sub_df.head(limit)
    # Pick top 6 most informative columns
    key_cols = [c for c in df.columns if any(kw in c.lower() for kw in ("brand", "dept", "item", "qty", "sale", "stock", "profit", "mrp", "vendor"))][:6]
    if not key_cols:
        key_cols = df.columns[:6]

    rows = []
    for r in sample.to_dicts():
        row_vals = [str(r.get(c, "-")) for c in key_cols]
        rows.append(row_vals)

    facts = {
        "records_matched": len(sub_df),
        "displayed_count": len(rows),
        "search_column": search_column,
        "keyword": keyword,
        "samples": sample.select(key_cols).to_dicts(),
    }

    return {
        "tool_name": "inspect_raw_sample",
        "title": f"Raw Records Sample ({len(sub_df):,} matching rows)",
        "table_headers": key_cols,
        "table_rows": rows,
        "facts": facts,
    }


def execute_inventory_status(
    dataset: Dataset,
    db: Session,
    product_query: Optional[str] = None,
    risk_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Looks up inventory decisions, reorder points, and stockout risk."""
    q = db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset.id)
    if product_query:
        q = q.filter(ProductForecast.product_id.ilike(f"%{product_query}%"))
    forecasts = q.all()

    items = []
    for f in forecasts:
        adv = f.inventory_advice or {}
        risk = adv.get("stockout_risk", "low")
        if risk_filter and risk_filter.lower() not in risk.lower():
            continue
        items.append({
            "product": f.product_id,
            "chosen_model": f.chosen_model,
            "reorder_point": int(round(adv.get("reorder_point", 0))),
            "recommended_qty": adv.get("recommended_order_qty", 0) or 0,
            "stockout_risk": risk,
            "days_of_cover": adv.get("days_of_cover"),
        })

    # Sort items by recommended order quantity descending
    items.sort(key=lambda x: x["recommended_qty"], reverse=True)
    top_items = items[:8]

    table_rows = [
        [
            it["product"],
            it["stockout_risk"].upper(),
            f"{it['reorder_point']:,}",
            f"{it['recommended_qty']:,}",
            it["chosen_model"],
        ]
        for it in top_items
    ]

    facts = {
        "total_analyzed": len(items),
        "items": top_items,
    }

    return {
        "tool_name": "inventory_status_lookup",
        "title": f"Inventory Sizing & Reorder Status ({len(items)} items)",
        "table_headers": ["Product", "Stockout Risk", "Reorder Point", "Order Qty", "Model"],
        "table_rows": table_rows,
        "facts": facts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────

def execute_dataset_tool(
    dataset: Dataset,
    tool_name: str,
    args: Dict[str, Any],
    db: Session,
) -> Dict[str, Any]:
    """
    Executes a chosen tool on the dataset deterministically with Polars and math.
    """
    col_map = dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})

    if tool_name == "inventory_status_lookup":
        return execute_inventory_status(
            dataset=dataset,
            db=db,
            product_query=args.get("product_query"),
            risk_filter=args.get("risk_filter"),
        )

    # All other tools operate on the dataset dataframe
    try:
        df = get_dataset_dataframe(dataset)
    except Exception as e:
        return {"error": f"Failed to access dataset: {str(e)}"}

    if tool_name == "rank_top_bottom":
        return execute_rank_top_bottom(
            df=df,
            dimension=args.get("dimension") or "BRAND",
            metric=args.get("metric") or "Profit Amt",
            n=args.get("n", 5),
            ascending=bool(args.get("ascending", False)),
            col_semantics=col_semantics,
        )
    elif tool_name == "filter_and_aggregate":
        return execute_filter_and_aggregate(
            df=df,
            target_metric=args.get("target_metric") or "Sale Qty",
            agg_func=args.get("agg_func") or "sum",
            filter_column=args.get("filter_column"),
            filter_value=args.get("filter_value"),
            group_by=args.get("group_by"),
            col_semantics=col_semantics,
        )
    elif tool_name == "column_distribution_stats":
        return execute_column_distribution_stats(
            df=df,
            column=args.get("column") or df.columns[0],
            col_semantics=col_semantics,
        )
    elif tool_name == "compare_groups":
        return execute_compare_groups(
            df=df,
            dimension=args.get("dimension") or "BRAND",
            entities=args.get("entities") or [],
            col_semantics=col_semantics,
        )
    elif tool_name == "inspect_raw_sample":
        return execute_inspect_raw_sample(
            df=df,
            search_column=args.get("search_column"),
            keyword=args.get("keyword"),
            limit=args.get("limit", 5),
            col_semantics=col_semantics,
        )
    else:
        return {"error": f"Unknown tool: '{tool_name}'"}


# ─────────────────────────────────────────────────────────────────────────────
# AGENTIC TOOL SELECTION (Gemma AI Agent Decides Which Tool to Use)
# ─────────────────────────────────────────────────────────────────────────────

async def select_tool_with_gemma(
    query: str,
    available_columns: List[str],
    col_semantics: Dict[str, str],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Prompts local Gemma to decide which tool to call based on the user's question.
    Returns (tool_name, arguments_dict) or None if no tool is required.
    """
    system_prompt = (
        "You are an AI data assistant with access to tools for querying a business dataset. "
        "Your task is to select the BEST tool to run on the dataset to answer the user's question.\n\n"
        "AVAILABLE TOOLS:\n"
        "1. rank_top_bottom: Use when user asks for top/bottom, highest/lowest, best/worst, most/least (e.g. 'top 5 brands by profit', 'lowest selling products').\n"
        "   Args: {\"dimension\": str, \"metric\": str, \"n\": int, \"ascending\": bool}\n"
        "2. filter_and_aggregate: Use when user asks for total, sum, average, min, max, or count of a metric, optionally filtered (e.g. 'total profit for Mens', 'average sale of brand X', 'how much revenue').\n"
        "   Args: {\"target_metric\": str, \"agg_func\": \"sum\"|\"mean\"|\"min\"|\"max\", \"filter_column\": str|null, \"filter_value\": str|null, \"group_by\": str|null}\n"
        "3. column_distribution_stats: Use when user asks for statistics, distribution, median, range, or breakdown of a single column.\n"
        "   Args: {\"column\": str}\n"
        "4. compare_groups: Use when user wants to compare two or more entities side-by-side (e.g. 'compare Brand A and Brand B').\n"
        "   Args: {\"dimension\": str, \"entities\": [str, str]}\n"
        "5. inspect_raw_sample: Use when user asks to search or view actual rows/records for a specific item, SKU, code, or department.\n"
        "   Args: {\"search_column\": str, \"keyword\": str, \"limit\": int}\n"
        "6. inventory_status_lookup: Use when user asks about stockout risk, reorder points, recommended orders, or lead times.\n"
        "   Args: {\"product_query\": str|null, \"risk_filter\": str|null}\n\n"
        f"Available dataset columns: {available_columns[:18]}\n\n"
        "Respond ONLY in valid JSON format:\n"
        "{\"tool\": \"tool_name\", \"arguments\": {...}}\n"
        "If NO tool is needed (e.g. greeting, general explanation of how forecasting works, what is TimesFM), respond with: {\"tool\": null}"
    )

    user_prompt = f"User Question: \"{query}\"\nSelect tool:"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("response", "").strip()
                parsed = json.loads(raw)
                tool = parsed.get("tool")
                if tool and tool in [t["name"] for t in TOOL_DEFINITIONS]:
                    return tool, parsed.get("arguments", {})
    except Exception as e:
        logger.warning(f"Gemma tool selection error: {e}")

    # Deterministic fallback heuristics if Ollama is busy or returns null on obvious tool questions
    query_lower = query.lower()

    # Ranking questions ("top 5", "highest profit", "bottom 3", "most sold", "best brand")
    if any(w in query_lower for w in ["top", "bottom", "highest", "lowest", "best", "worst", "most", "least"]):
        ascending = any(w in query_lower for w in ["bottom", "lowest", "worst", "least"])
        # Extract number if present
        m_num = re.search(r"\b(\d+)\b", query_lower)
        n = int(m_num.group(1)) if m_num and int(m_num.group(1)) <= 20 else 5

        # Infer dimension
        dim = "BRAND"
        if any(w in query_lower for w in ["dept", "department", "section", "category"]):
            dim = "DEPARTMENT"
        elif any(w in query_lower for w in ["vendor", "supplier", "party"]):
            dim = "Vendor"

        # Infer metric
        metric = "Profit Amt" if any(w in query_lower for w in ["profit", "margin"]) else (
            "Closing Qty" if any(w in query_lower for w in ["stock", "closing", "inventory"]) else "Sale Qty"
        )
        return "rank_top_bottom", {"dimension": dim, "metric": metric, "n": n, "ascending": ascending}

    # Aggregate questions ("total profit", "how much sold", "average price", "sum of sales")
    if any(w in query_lower for w in ["total", "sum", "average", "avg", "mean", "how much", "how many"]):
        agg_func = "mean" if any(w in query_lower for w in ["average", "avg", "mean"]) else "sum"
        metric = "Profit Amt" if any(w in query_lower for w in ["profit", "margin"]) else (
            "Gross Item Sale" if any(w in query_lower for w in ["revenue", "sales value", "turnover"]) else "Sale Qty"
        )
        return "filter_and_aggregate", {"target_metric": metric, "agg_func": agg_func}

    # Comparison questions ("compare X and Y")
    if "compare" in query_lower or " vs " in query_lower or " versus " in query_lower:
        return "compare_groups", {"dimension": "BRAND", "entities": []}

    return None

