import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import httpx
import numpy as np
import polars as pl
from sqlalchemy.orm import Session

from apps.api.app.config import settings
from apps.api.app.db import Dataset, ProductForecast
from apps.api.app.services.cleaning import (
    _is_non_additive_metric,
    detect_date_format_ambiguity,
    infer_natural_frequency,
    parse_date_column,
)
from apps.api.app.services.column_mapping import NUMERIC_DTYPES, check_date_parse_rate
from apps.api.app.services.ingestion import read_raw_dataframe

logger = logging.getLogger("advisor.tools")

# In-memory LRU-like cache for dataset DataFrames: {dataset_id: (timestamp, df)}
_DF_CACHE: Dict[str, Tuple[float, pl.DataFrame]] = {}
_DUCK_DF_CACHE: Dict[str, Tuple[float, pl.DataFrame]] = {}
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


def get_duckdb_ready_dataframe(dataset: Dataset, raw_df: Optional[pl.DataFrame] = None) -> pl.DataFrame:
    """
    Returns a copy of the dataset DataFrame where string date columns (like '30/09/2007')
    are parsed into native pl.Date so DuckDB temporal queries (YEAR, ORDER BY date, BETWEEN)
    work accurately out of the box.
    """
    now = time.time()
    if dataset.id in _DUCK_DF_CACHE:
        ts, cached = _DUCK_DF_CACHE[dataset.id]
        if now - ts < _CACHE_TTL_SECONDS:
            return cached

    df = raw_df if raw_df is not None else get_dataset_dataframe(dataset)
    col_map = dataset.column_mapping or {}
    profiles = col_map.get("_column_profiles") or {}

    exprs = []
    for col in df.columns:
        if df[col].dtype in (pl.Utf8, pl.String):
            is_temp_prof = profiles.get(col, {}).get("role") == "temporal" or col == col_map.get("date")
            if is_temp_prof or check_date_parse_rate(df, col) >= 0.80:
                pref = detect_date_format_ambiguity(df, col)
                parsed = parse_date_column(df[col], pref)
                if parsed.drop_nulls().len() >= max(1, int(df[col].drop_nulls().len() * 0.75)):
                    exprs.append(parsed.alias(col))

    out_df = df.with_columns(exprs) if exprs else df
    if len(_DUCK_DF_CACHE) >= 10:
        oldest_id = min(_DUCK_DF_CACHE.keys(), key=lambda k: _DUCK_DF_CACHE[k][0])
        del _DUCK_DF_CACHE[oldest_id]
    _DUCK_DF_CACHE[dataset.id] = (now, out_df)
    return out_df


_FORBIDDEN_SQL_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|ATTACH|DETACH|"
    r"COPY|EXPORT|IMPORT|PRAGMA|INSTALL|LOAD|CALL|GRANT|REVOKE|VACUUM|"
    r"read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_ndjson|glob)\b",
    re.IGNORECASE,
)


def validate_readonly_sql(sql: str) -> str:
    """Validates and sanitizes a read-only DuckDB SELECT/WITH query over `data`."""
    if not sql or not str(sql).strip():
        raise ValueError("Empty SQL query.")
    cleaned = str(sql).strip()
    # Strip markdown code fences if LLM included them
    cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Remove SQL comments
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip().rstrip(";").strip()

    if ";" in cleaned:
        raise ValueError("Only a single read-only SELECT statement is allowed.")
    if not re.match(r"^(?:SELECT|WITH)\b", cleaned, re.IGNORECASE):
        raise ValueError("SQL query must start with SELECT or WITH.")
    if _FORBIDDEN_SQL_RE.search(cleaned):
        raise ValueError("Forbidden SQL keyword detected; only read-only analytical SELECT queries on `data` are permitted.")
    return cleaned


def _auto_quote_column_identifiers(sql: str, columns: List[str]) -> str:
    """Safely quotes unquoted multi-word or special-character column names in SQL if LLM omitted double quotes."""
    special_cols = sorted(
        [c for c in columns if re.search(r"[^A-Za-z0-9_]", c)],
        key=len,
        reverse=True,
    )
    fixed = sql
    for col in special_cols:
        # Match unquoted occurrences of col (not already inside "..." or '...')
        pattern = rf'(?<!["\w]){re.escape(col)}(?!["\w])'
        fixed = re.sub(pattern, f'"{col}"', fixed)
    return fixed


def run_duckdb_df(df: pl.DataFrame, sql: str) -> pl.DataFrame:
    """Executes a validated read-only SQL query against `df` registered as table `data`."""
    safe_sql = validate_readonly_sql(sql)
    safe_sql = _auto_quote_column_identifiers(safe_sql, df.columns)
    con = duckdb.connect(":memory:")
    try:
        con.register("data", df)
        rel = con.sql(safe_sql)
        return rel.pl()
    finally:
        con.close()
def normalize_col_token(token: str) -> str:
    """Normalizes string to lowercase alphanumeric underscore."""
    return re.sub(r"[^a-z0-9]+", "_", token.strip().lower()).strip("_")


def resolve_column_name(
    available_cols: List[str],
    target: str,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Dynamically maps an informal, partial, or domain-synonym column reference to the exact
    column name in `available_cols` for ANY dataset domain (finance, HR, healthcare, IoT, retail, etc.)
    using strict token boundaries and fuzzy similarity (avoiding short-substring collisions).
    """
    if not target or not available_cols:
        return None

    # 1. Exact match
    if target in available_cols:
        return target

    target_norm = normalize_col_token(target)
    if not target_norm:
        return None

    # 2. Normalized exact match
    norm_map = {normalize_col_token(c): c for c in available_cols}
    if target_norm in norm_map:
        return norm_map[target_norm]

    target_tokens = [t for t in target_norm.split("_") if t]
    col_tokens_map = {orig_c: [t for t in norm_c.split("_") if t] for norm_c, orig_c in norm_map.items()}

    # 3. Token overlap match (whole tokens only — prevents 'ma' matching inside 'margin' or 'email')
    best_overlap_col = None
    best_overlap_score = 0.0
    for orig_c, c_toks in col_tokens_map.items():
        if not c_toks:
            continue
        common = set(target_tokens) & set(c_toks)
        if common:
            score = len(common) / max(len(target_tokens), len(c_toks)) + 0.25 * len(common)
            if score > best_overlap_score:
                best_overlap_score = score
                best_overlap_col = orig_c
    if best_overlap_col and best_overlap_score >= 0.65:
        return best_overlap_col

    # 4. Multi-domain synonym groups (token-safe matching)
    synonyms = {
        "profit": ["profit_amt", "profit", "net_profit", "gross_profit", "earnings", "ebitda", "income"],
        "margin": ["profit_pct", "profit_margin", "gross_margin", "margin", "profit_amt"],
        "sales": ["net_sale_qty", "sale_qty", "sales_qty", "units_sold", "sales", "quantity", "sold_qty"],
        "quantity": ["net_sale_qty", "sale_qty", "sales_qty", "units_sold", "quantity", "qty", "units", "count", "volume"],
        "units": ["net_sale_qty", "sale_qty", "sales_qty", "units_sold", "units", "quantity"],
        "sold": ["net_sale_qty", "sale_qty", "sales_qty", "units_sold", "sold"],
        "revenue": ["gross_item_sale", "gross_sales", "sale_value", "revenue", "net_sales", "turnover", "arr", "mrr", "amount", "billing"],
        "turnover": ["gross_item_sale", "gross_sales", "revenue", "turnover"],
        "stock": ["closing_qty", "closing_stock", "closing", "stock_on_hand", "soh", "current_stock", "inventory"],
        "closing": ["closing_qty", "closing_stock", "closing", "close"],
        "opening": ["opening_qty", "opening", "op_qty", "open"],
        "inward": ["net_inward_qty", "inward", "receipt"],
        "outward": ["net_outward_qty", "outward", "transfer"],
        "returns": ["sale_rtn_qty", "return_qty", "returned"],
        "discount": ["discount_amt", "discount_amount", "discount", "disc", "markdown"],
        "cost": ["cost_price", "cl_value_in_rate", "cogs", "cost", "purchase_cost", "expense"],
        "price": ["price", "unit_price", "selling_price", "mrp", "rate", "close", "ma"],
        "salary": ["salary", "compensation", "wage", "pay", "CTC", "income"],
        "brand": ["brand", "manufacturer", "label", "make"],
        "product": ["product", "department", "sku", "item", "article", "style", "model", "name"],
        "category": ["main_category", "category", "section", "division", "department", "type", "segment", "class"],
        "department": ["department", "section", "category", "division", "team"],
        "vendor": ["vendor", "supplier", "party", "dealer"],
        "customer": ["customer", "client", "user", "patient", "student", "employee", "account"],
        "location": ["city", "state", "country", "region", "location", "store", "branch", "site"],
        "offtake": ["offtake_by_qty", "offtake_pct", "sell_through_rate", "sell_through"],
    }

    for syn_key, candidates in synonyms.items():
        if syn_key == target_norm or syn_key in target_tokens:
            for cand in candidates:
                cand_norm = cand.lower()
                for norm_c, orig_c in norm_map.items():
                    c_toks = col_tokens_map[orig_c]
                    if cand_norm == norm_c or cand_norm in c_toks or (len(cand_norm) >= 4 and cand_norm in norm_c):
                        return orig_c

    # 5. Substring containment ONLY when token length >= 4 (safe against 1-3 char false hits)
    if len(target_norm) >= 4:
        for norm_c, orig_c in norm_map.items():
            if target_norm in norm_c or (len(norm_c) >= 4 and norm_c in target_norm):
                return orig_c

    # 6. Check semantic descriptions (whole token match, len >= 4)
    if col_semantics and len(target_norm) >= 4:
        for orig_c, desc in col_semantics.items():
            desc_tokens = set(normalize_col_token(desc).split("_"))
            if orig_c in available_cols and (target_norm in desc_tokens or set(target_tokens) <= desc_tokens):
                return orig_c

    # 7. Typo-tolerant fuzzy match (e.g. 'bedroms' -> 'bedrooms', 'salry' -> 'salary')
    import difflib

    best_fuzzy_col = None
    best_ratio = 0.0
    for norm_c, orig_c in norm_map.items():
        ratio = difflib.SequenceMatcher(None, target_norm, norm_c).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_fuzzy_col = orig_c
    if best_fuzzy_col and best_ratio >= 0.78:
        return best_fuzzy_col

    return None


def discover_query_context(
    query: str,
    df: pl.DataFrame,
    col_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Universal, domain-agnostic query grounder!
    Scans `df` column names, data types, actual distinct cell values, and numeric predicates
    so ANY dataset (healthcare, HR, SaaS, finance, IoT, housing, retail) maps cleanly to SQL/tools.
    """
    q_low = query.lower()
    q_norm_tokens = set(normalize_col_token(query).split("_"))

    mentioned_measures: List[str] = []
    mentioned_dimensions: List[str] = []
    mentioned_temporals: List[str] = []

    for col in df.columns:
        c_low = col.lower()
        c_norm = normalize_col_token(col)
        c_toks = [t for t in c_norm.split("_") if len(t) >= 2]
        prof = (col_profiles or {}).get(col, {})
        role = prof.get("role", "")

        direct_hit = bool(re.search(rf"(?:^|\W){re.escape(c_low)}(?:$|\W)", q_low))
        singular_hit = any(
            len(t) >= 3 and (t in q_norm_tokens or f"{t}s" in q_norm_tokens or (t.endswith("s") and t[:-1] in q_norm_tokens))
            for t in c_toks
        )
        if direct_hit or singular_hit:
            if role == "temporal" or df[col].dtype in (pl.Date, pl.Datetime):
                mentioned_temporals.append(col)
            elif df[col].dtype in NUMERIC_DTYPES and role != "numeric_attribute":
                mentioned_measures.append(col)
            else:
                mentioned_dimensions.append(col)

    # If no explicit measure matched by exact column name, try domain synonym resolution on query words
    if not mentioned_measures:
        num_cols = [c for c in df.columns if df[c].dtype in NUMERIC_DTYPES]
        for tok in q_norm_tokens:
            if len(tok) >= 4:
                resolved = resolve_column_name(num_cols, tok, col_semantics)
                if resolved and resolved not in mentioned_measures and resolved not in mentioned_dimensions:
                    mentioned_measures.append(resolved)

    # Scan actual distinct categorical values in df (up to 400 unique values per column) to find entity filters!
    matched_value_filters: List[Dict[str, Any]] = []
    for col in df.columns:
        if df[col].dtype in (pl.Utf8, pl.String, pl.Categorical):
            n_u = int(df[col].drop_nulls().n_unique())
            if 1 <= n_u <= 400:
                vals = df[col].drop_nulls().unique().to_list()
                # Sort longer strings first so 'CASUAL SHIRT F/S' matches before 'SHIRT'
                vals_sorted = sorted((str(v).strip() for v in vals if str(v).strip()), key=len, reverse=True)
                col_hits = []
                for v_str in vals_sorted:
                    if len(v_str) >= 2 and re.search(rf"(?:^|\b){re.escape(v_str.lower())}(?:\b|$)", q_low):
                        # Avoid matching generic English stop words
                        if v_str.lower() in {"in", "on", "to", "by", "or", "is", "no", "na", "all", "top", "the", "and", "for"}:
                            continue
                        col_hits.append(v_str)
                        if len(col_hits) >= 4:
                            break
                if col_hits:
                    matched_value_filters.append({"column": col, "values": col_hits})

    # Detect numeric attribute equality like "3 bedrooms" or "bedrooms = 3"
    numeric_predicates: List[str] = []
    for col in df.columns:
        if df[col].dtype in NUMERIC_DTYPES:
            c_esc = re.escape(col.lower())
            c_sing = re.escape(col.lower().rstrip("s"))
            # Pattern: "<col> > 10", "<col> >= 5", "<col> = 3"
            m_op = re.search(rf"(?:{c_esc}|{c_sing})\s*(>=|<=|!=|>|<|=|is|above|over|more than|greater than|below|under|less than)\s*(-?\d+(?:\.\d+)?)", q_low)
            if m_op:
                raw_op, num_s = m_op.group(1), m_op.group(2)
                sql_op = {
                    "is": "=", "above": ">", "over": ">", "more than": ">", "greater than": ">",
                    "below": "<", "under": "<", "less than": "<",
                }.get(raw_op, raw_op)
                numeric_predicates.append(f'"{col}" {sql_op} {num_s}')
                continue
            # Pattern: "3 bedrooms"
            m_pre = re.search(rf"\b(\d+)\s+(?:{c_esc}|{c_sing})\b", q_low)
            if m_pre and (col_profiles or {}).get(col, {}).get("role") == "numeric_attribute":
                numeric_predicates.append(f'"{col}" = {m_pre.group(1)}')

    return {
        "mentioned_measures": mentioned_measures,
        "mentioned_dimensions": mentioned_dimensions,
        "mentioned_temporals": mentioned_temporals,
        "matched_value_filters": matched_value_filters,
        "numeric_predicates": numeric_predicates,
    }


def build_dynamic_sql_from_query(
    query: str,
    df: pl.DataFrame,
    col_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[str, str]]:
    """
    Synthesizes an accurate read-only DuckDB SELECT query dynamically for ANY dataset schema
    using discovered columns, actual cell values, temporal bucketing, and aggregation semantics.
    Returns (sql_string, title) or None if query is purely conversational.
    """
    q_low = query.lower().strip()
    ctx = discover_query_context(query, df, col_profiles, col_semantics)

    all_measures = [c for c in df.columns if df[c].dtype in NUMERIC_DTYPES and (col_profiles or {}).get(c, {}).get("role") != "numeric_attribute"]
    if not all_measures:
        all_measures = [c for c in df.columns if df[c].dtype in NUMERIC_DTYPES]
    all_dims = [c for c in df.columns if (col_profiles or {}).get(c, {}).get("role") in ("categorical", "numeric_attribute", "identifier")]
    all_dates = [c for c in df.columns if df[c].dtype in (pl.Date, pl.Datetime) or (col_profiles or {}).get(c, {}).get("role") == "temporal"]

    target_measures = ctx["mentioned_measures"] or all_measures[:2]
    primary_m = target_measures[0] if target_measures else None

    # Build WHERE predicates
    where_parts: List[str] = list(ctx["numeric_predicates"])
    is_compare = "compare" in q_low or " vs " in q_low or " versus " in q_low
    group_dims: List[str] = []

    for vf in ctx["matched_value_filters"]:
        col = vf["column"]
        vals = vf["values"]
        if len(vals) >= 2 or is_compare:
            in_list = ", ".join("'" + v.lower().replace("'", "''") + "'" for v in vals)
            where_parts.append(f'LOWER(CAST("{col}" AS VARCHAR)) IN ({in_list})')
            if col not in group_dims:
                group_dims.append(col)
        elif len(vals) == 1 and col not in ctx["mentioned_dimensions"]:
            safe_v = vals[0].lower().replace("'", "''")
            where_parts.append(f'LOWER(CAST("{col}" AS VARCHAR)) = \'{safe_v}\'')

    # Temporal grouping check ("by year", "yearly", "by month", "monthly", "by quarter", "trend over time")
    time_expr = None
    time_alias = None
    if all_dates:
        d_col = ctx["mentioned_temporals"][0] if ctx["mentioned_temporals"] else all_dates[0]
        if any(w in q_low for w in ("by year", "yearly", "per year", "each year", "annual", "year over year", "yoy")):
            time_expr = f'EXTRACT(YEAR FROM "{d_col}")'
            time_alias = "year"
        elif any(w in q_low for w in ("by quarter", "quarterly", "per quarter", "qoq")):
            time_expr = f'STRFTIME("{d_col}", \'%Y-Q\') || CAST(QUARTER("{d_col}") AS VARCHAR)'
            time_alias = "quarter"
        elif any(w in q_low for w in ("by month", "monthly", "per month", "each month", "mom")):
            time_expr = f'STRFTIME("{d_col}", \'%Y-%m\')'
            time_alias = "month"
        elif any(w in q_low for w in ("over time", "time trend", "historical trend", "timeline")):
            time_expr = f'"{d_col}"'
            time_alias = d_col

    # Add explicitly requested grouping dimensions ("by X", "per X", "across X", or top/bottom X)
    if any(w in q_low for w in (" by ", " per ", " across ", " each ", "top ", "bottom ", "highest ", "lowest ", "most ", "least ", "compare", "breakdown")):
        for d in ctx["mentioned_dimensions"]:
            if d not in group_dims:
                group_dims.append(d)
        if not group_dims and not time_expr and any(w in q_low for w in ("top ", "bottom ", "highest ", "lowest ", "best ", "worst ", "breakdown")):
            if all_dims:
                group_dims.append(all_dims[0])

    # Determine aggregation function
    if any(w in q_low for w in ("median",)):
        agg_fn = "MEDIAN"
    elif any(w in q_low for w in ("average", "avg", "mean")):
        agg_fn = "AVG"
    elif any(w in q_low for w in ("minimum", "min ", "lowest value")):
        agg_fn = "MIN"
    elif any(w in q_low for w in ("maximum", "max ", "peak", "highest value")) and not group_dims:
        agg_fn = "MAX"
    elif any(w in q_low for w in ("how many", "count", "number of")) and not ctx["mentioned_measures"]:
        agg_fn = "COUNT"
    elif primary_m and _is_non_additive_metric(primary_m):
        agg_fn = "AVG"
    else:
        agg_fn = "SUM"

    ascending = any(w in q_low for w in ("bottom", "lowest", "worst", "least", "smallest", "ascending"))
    m_lim = re.search(r"\b(?:top|bottom|first|last|limit)\s+(\d+)\b", q_low)
    limit_n = min(int(m_lim.group(1)), 50) if m_lim else 15

    where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""

    if time_expr or group_dims:
        select_dims = []
        group_refs = []
        idx_counter = 1
        if time_expr and time_alias:
            select_dims.append(f'{time_expr} AS "{time_alias}"')
            group_refs.append(str(idx_counter))
            idx_counter += 1
        for gd in group_dims[:2]:
            select_dims.append(f'"{gd}"')
            group_refs.append(str(idx_counter))
            idx_counter += 1
            if not where_parts:
                where_sql = f' WHERE "{gd}" IS NOT NULL'

        agg_cols = []
        if agg_fn == "COUNT" or not primary_m:
            agg_cols.append("COUNT(*) AS record_count")
            order_col = "record_count"
        else:
            for m_c in target_measures[:3]:
                fn_use = "AVG" if (_is_non_additive_metric(m_c) and agg_fn == "SUM") else agg_fn
                alias = f"{fn_use.lower()}_{normalize_col_token(m_c)}"
                agg_cols.append(f'ROUND({fn_use}("{m_c}"), 2) AS "{alias}"')
            agg_cols.append("COUNT(*) AS records")
            first_fn = "AVG" if (_is_non_additive_metric(primary_m) and agg_fn == "SUM") else agg_fn
            order_col = f'"{first_fn.lower()}_{normalize_col_token(primary_m)}"'

        order_sql = f'"{time_alias}" ASC' if (time_expr and not any(w in q_low for w in ("top ", "bottom ", "highest", "lowest"))) else f"{order_col} {'ASC' if ascending else 'DESC'}"
        sql = f"SELECT {', '.join(select_dims + agg_cols)} FROM data{where_sql} GROUP BY {', '.join(group_refs)} ORDER BY {order_sql} LIMIT {limit_n}"
        title = f"Dynamic Analysis: {', '.join(group_dims or [time_alias or 'Group'])}"
        return sql, title

    if primary_m and (where_parts or any(w in q_low for w in ("total", "sum", "average", "avg", "mean", "median", "min", "max", "how much", "how many", "count"))):
        sql = (
            f'SELECT COUNT(*) AS records, '
            f'ROUND(SUM("{primary_m}"), 2) AS total_sum, '
            f'ROUND(AVG("{primary_m}"), 2) AS average, '
            f'ROUND(MEDIAN("{primary_m}"), 2) AS median, '
            f'ROUND(MIN("{primary_m}"), 2) AS minimum, '
            f'ROUND(MAX("{primary_m}"), 2) AS maximum '
            f'FROM data{where_sql}'
        )
        return sql, f"Summary Metrics for {primary_m}"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DEFINITIONS (Exposed to Gemma AI Agent)
# ─────────────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "run_sql_query",
        "description": "Executes ANY read-only DuckDB SQL analytical query (SELECT / WITH, multi-condition WHERE, GROUP BY, HAVING, window functions RANK/LAG, ratios, correlations, pivots, date bucketing, percentiles) on table `data`.",
        "parameters": {
            "sql": "Valid DuckDB SELECT query against table `data` with double-quoted column names (e.g. SELECT \"type\", AVG(\"MA\") AS avg_ma FROM data WHERE \"bedrooms\" = 3 GROUP BY 1 ORDER BY 2 DESC)",
            "title": "Short descriptive title for the query result",
        },
    },
    {
        "name": "forecast_future",
        "description": "Generates future time-series predictions (with 80% confidence intervals, trend chart, and downloadable CSV) for the overall dataset, a specific numeric column, or a filtered entity/subset.",
        "parameters": {
            "target_metric": "Numeric column to forecast (e.g. 'MA', 'Units_Sold', 'Revenue', 'Sale Qty', 'Profit Amt')",
            "horizon": "Number of future periods to predict (integer, optional; auto-selected by frequency if null)",
            "filter_column": "Optional categorical/attribute column to filter on (e.g. 'type', 'Product_SKU', 'BRAND')",
            "filter_value": "Optional value to filter for (e.g. 'house', 'PROD_01')",
        },
    },
    {
        "name": "generate_csv_export",
        "description": "Generates a brand-new downloadable CSV and Excel (.xlsx) file containing filtered, transformed, calculated, aggregated, or forecasted data requested by the user.",
        "parameters": {
            "sql": "DuckDB SELECT query on table `data` producing the exact rows and columns requested for the new CSV (leave null if exporting future predictions)",
            "filename_slug": "Descriptive short filename without extension (e.g. 'high_profit_brands', 'houses_3bed_summary')",
            "include_forecasts": "Boolean: true ONLY if user specifically asked for a CSV of future predictions/forecasts",
            "description": "Short summary of what is inside the generated CSV",
        },
    },
    {
        "name": "analyze_correlations",
        "description": "Computes statistical correlations (Pearson r) and key numeric/categorical drivers for a target metric or across all numeric columns.",
        "parameters": {
            "target_column": "Optional target numeric column to find drivers/correlations for",
        },
    },
    {
        "name": "detect_anomalies",
        "description": "Detects extreme statistical outliers and anomalous records (IQR and Z-score > 2.5) in a specific numeric column or across the dataset.",
        "parameters": {
            "column": "Optional numeric column to check for anomalies/outliers",
            "limit": "Max anomalous records to display (default 10)",
        },
    },
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
        "description": "Performs side-by-side comparison between 2 or more entities (e.g. compare Brand A vs Brand B, or house vs unit) across numeric metrics.",
        "parameters": {
            "dimension": "The categorical column (e.g. 'BRAND', 'type', 'DEPARTMENT')",
            "entities": "List of 2 to 5 entity names to compare (e.g. ['house', 'unit'])",
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

    use_mean = _is_non_additive_metric(metric_col)
    agg_expr = (
        pl.col(metric_col).drop_nulls().cast(pl.Float64).mean().alias("metric_value")
        if use_mean
        else pl.col(metric_col).fill_null(0.0).cast(pl.Float64).sum().alias("metric_value")
    )

    grouped = (
        work.group_by(dim_col)
        .agg([
            agg_expr,
            pl.len().alias("record_count"),
        ])
        .filter(pl.col("metric_value").is_not_null())
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
    agg_prefix = "Avg " if use_mean else ""
    title = f"{direction} {len(results)} {dim_col} by {agg_prefix}{metric_col}"

    computed_insights = _compute_table_insights(grouped.select([dim_col, "metric_value"]))

    facts: Dict[str, Any] = {
        "ranking_direction": "bottom" if ascending else "top",
        "aggregation": "mean" if use_mean else "sum",
        "dimension": dim_col,
        "metric": metric_col,
        "count": len(results),
        "results": results,
    }
    if computed_insights:
        facts["computed_insights"] = computed_insights

    return {
        "tool_name": "rank_top_bottom",
        "title": title,
        "dimension_column": dim_col,
        "metric_column": metric_col,
        "table_headers": [dim_col, f"{agg_prefix}{metric_col}", "Records"],
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
    """Compares multiple entities across key numeric metrics in any dataset."""
    dim_col = resolve_column_name(df.columns, dimension, col_semantics) or df.columns[0]
    sale_col = resolve_column_name(df.columns, "sales", col_semantics)
    stock_col = resolve_column_name(df.columns, "stock", col_semantics)
    profit_col = resolve_column_name(df.columns, "profit", col_semantics)
    rev_col = resolve_column_name(df.columns, "revenue", col_semantics)

    # Check whether this is a retail inventory/sales table or a general numeric dataset
    has_retail_cols = bool(sale_col or stock_col or profit_col or rev_col)
    numeric_cols = [c for c in df.columns if c != dim_col and df[c].dtype in NUMERIC_DTYPES]

    if not entities or not isinstance(entities, list):
        entities = []

    patterns = [str(e).strip() for e in entities if str(e).strip()]
    if not patterns:
        top_ent = (
            df.filter(pl.col(dim_col).is_not_null())
            .group_by(dim_col)
            .len()
            .sort("len", descending=True)
            .head(3)[dim_col]
            .to_list()
        )
        patterns = [str(x) for x in top_ent]

    available_top = [
        str(x)
        for x in (
            df.filter(pl.col(dim_col).is_not_null())
            .group_by(dim_col)
            .len()
            .sort("len", descending=True)
            .head(8)[dim_col]
            .to_list()
        )
    ]

    comparison_results = []
    table_rows = []
    chart_data = []
    unmatched_entities = []

    if has_retail_cols:
        for ent in patterns:
            ent_low = ent.lower()
            sub_df = df.filter(pl.col(dim_col).cast(pl.Utf8).str.to_lowercase().str.contains(ent_low, literal=True))
            if len(sub_df) == 0:
                unmatched_entities.append(ent)
                continue
            actual_name = str(sub_df[dim_col].drop_nulls().head(1)[0])
            sold = round(float(sub_df[sale_col].fill_null(0.0).cast(pl.Float64).sum()), 1) if sale_col else 0.0
            stock = round(float(sub_df[stock_col].fill_null(0.0).cast(pl.Float64).sum()), 1) if stock_col else 0.0
            profit = round(float(sub_df[profit_col].fill_null(0.0).cast(pl.Float64).sum()), 2) if profit_col else 0.0
            rev = round(float(sub_df[rev_col].fill_null(0.0).cast(pl.Float64).sum()), 2) if rev_col else 0.0

            item_facts = {
                "entity": actual_name,
                "units_sold": sold,
                "stock_on_hand": stock,
                "total_profit": profit,
                "total_revenue": rev,
                "records": len(sub_df),
            }
            comparison_results.append(item_facts)
            chart_data.append({"name": actual_name, "value": sold, "sold": sold, "stock": stock})
            row = [actual_name, f"{sold:,.0f}", f"{stock:,.0f}"]
            if profit_col:
                row.append(f"{profit:,.2f}")
            if rev_col:
                row.append(f"{rev:,.2f}")
            table_rows.append(row)

        headers = [dim_col, "Units Sold", "Stock on Hand"]
        if profit_col:
            headers.append("Profit")
        if rev_col:
            headers.append("Revenue")
    else:
        # General dataset comparison (e.g. house vs unit across MA, bedrooms, etc.)
        chosen_metrics = numeric_cols[:4]
        headers = [dim_col, "Records"] + [f"Avg {m}" if _is_non_additive_metric(m) else f"Total {m}" for m in chosen_metrics]
        for ent in patterns:
            ent_low = ent.lower()
            sub_df = df.filter(pl.col(dim_col).cast(pl.Utf8).str.to_lowercase().str.contains(ent_low, literal=True))
            if len(sub_df) == 0:
                unmatched_entities.append(ent)
                continue
            actual_name = str(sub_df[dim_col].drop_nulls().head(1)[0])
            row = [actual_name, f"{len(sub_df):,}"]
            item_facts: Dict[str, Any] = {"entity": actual_name, "records": len(sub_df)}
            primary_chart_val = 0.0
            for m_idx, m_col in enumerate(chosen_metrics):
                s_num = sub_df[m_col].drop_nulls().cast(pl.Float64)
                val = float(s_num.mean() if _is_non_additive_metric(m_col) else s_num.sum()) if len(s_num) > 0 else 0.0
                val = round(val, 2)
                item_facts[m_col] = val
                row.append(f"{val:,.2f}")
                if m_idx == 0:
                    primary_chart_val = val
            comparison_results.append(item_facts)
            chart_data.append({"name": actual_name, "value": primary_chart_val})
            table_rows.append(row)

    # If none of the requested entities existed in dim_col, compare top actual entities instead of showing fake zeros!
    if not comparison_results and unmatched_entities and available_top:
        fallback_res = execute_compare_groups(df, dim_col, available_top[:3], col_semantics)
        fallback_res["facts"]["requested_entities_not_found"] = unmatched_entities
        fallback_res["facts"]["available_top_entities"] = available_top
        fallback_res["title"] = f"'{', '.join(unmatched_entities)}' not found in {dim_col} — Top {dim_col} Comparison"
        return fallback_res

    facts: Dict[str, Any] = {
        "dimension": dim_col,
        "compared_entities": comparison_results,
    }
    if unmatched_entities:
        facts["entities_not_found"] = unmatched_entities
        facts["available_top_entities"] = available_top

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
# ADVANCED DATA ANALYST TOOLS (DuckDB SQL, Live Forecasting, CSV Export, Drivers, Anomalies)
# ─────────────────────────────────────────────────────────────────────────────

def _format_cell(val: Any) -> str:
    if val is None:
        return "-"
    if isinstance(val, float):
        if np.isnan(val):
            return "-"
        return f"{val:,.2f}" if abs(val - round(val)) > 1e-6 else f"{int(round(val)):,}"
    if isinstance(val, int) and not isinstance(val, bool):
        return f"{val:,}"
    return str(val)


def _repair_sql_with_gemma(
    failed_sql: str,
    error_msg: str,
    df: pl.DataFrame,
    user_query: str,
) -> Optional[str]:
    schema_desc = ", ".join(f'"{c}" ({df[c].dtype})' for c in df.columns)
    prompt = (
        f"Fix this DuckDB SELECT query on table `data`.\n"
        f"User question: {user_query}\n"
        f"Exact columns in `data`: {schema_desc}\n"
        f"Failed SQL: {failed_sql}\n"
        f"DuckDB Error: {error_msg}\n\n"
        f"Rules: Always wrap column names in double quotes like \"Column Name\". "
        f"Return JSON: {{\"sql\": \"SELECT ... FROM data ...\"}}"
    )
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
            )
            if resp.status_code == 200:
                parsed = json.loads(resp.json().get("response", "{}"))
                return parsed.get("sql")
    except Exception:
        pass
    return None


def _compute_table_insights(df_res: pl.DataFrame) -> Dict[str, Any]:
    """
    Deterministically computes comparative analytical insights (#1 leader, #2 runner-up,
    lead % margin, % share of total, min/max spread) on any result DataFrame so the LLM
    can cite deeper insights with zero mental arithmetic errors.
    """
    insights: Dict[str, Any] = {}
    if len(df_res) == 0 or len(df_res.columns) == 0:
        return insights

    headers = list(df_res.columns)
    if len(df_res) >= 2 and len(headers) >= 2:
        label_col = headers[0]
        num_cols = [c for c in headers[1:] if df_res[c].dtype in NUMERIC_DTYPES]
        if num_cols:
            m_col = num_cols[0]
            s_vals = df_res[m_col].drop_nulls().cast(pl.Float64)
            if len(s_vals) >= 2:
                rows_d = df_res.select([label_col, m_col]).drop_nulls().to_dicts()
                if len(rows_d) >= 2:
                    sorted_d = sorted(rows_d, key=lambda r: float(r[m_col]), reverse=True)
                    top1_name, top1_val = str(sorted_d[0][label_col]), round(float(sorted_d[0][m_col]), 2)
                    top2_name, top2_val = str(sorted_d[1][label_col]), round(float(sorted_d[1][m_col]), 2)
                    bot_name, bot_val = str(sorted_d[-1][label_col]), round(float(sorted_d[-1][m_col]), 2)
                    total_pos = float(s_vals.sum())
                    insights["metric_analyzed"] = m_col
                    insights["leader"] = {"entity": top1_name, "value": top1_val}
                    insights["runner_up"] = {"entity": top2_name, "value": top2_val}
                    insights["lowest"] = {"entity": bot_name, "value": bot_val}
                    insights["spread_max_minus_min"] = round(top1_val - bot_val, 2)
                    if abs(top2_val) > 1e-9:
                        insights["leader_vs_runner_up_pct"] = round(((top1_val - top2_val) / abs(top2_val)) * 100.0, 2)
                    if total_pos > 0 and float(s_vals.min()) >= 0 and not _is_non_additive_metric(m_col):
                        insights["table_sum"] = round(total_pos, 2)
                        insights["leader_share_pct"] = round((top1_val / total_pos) * 100.0, 2)
    return insights


def _relax_sql_equality_to_ilike(sql: str) -> Optional[str]:
    """Self-reflection helper: relaxes strict string equality `= 'val'` to case-insensitive substring `ILIKE '%val%'`."""
    # Convert LOWER(CAST("col" AS VARCHAR)) = 'val' or "col" = 'val' -> CAST("col" AS VARCHAR) ILIKE '%val%'
    relaxed = re.sub(
        r'LOWER\(\s*CAST\(([^)]+)\s+AS\s+VARCHAR\)\s*\)\s*=\s*\'([^\']+)\'',
        r"CAST(\1 AS VARCHAR) ILIKE '%\2%'",
        sql,
        flags=re.IGNORECASE,
    )
    if relaxed == sql:
        relaxed = re.sub(
            r'("[^"]+"|\b[A-Za-z_][A-Za-z0-9_]*\b)\s*=\s*\'([^\']+)\'',
            r"CAST(\1 AS VARCHAR) ILIKE '%\2%'",
            sql,
        )
    return relaxed if relaxed != sql else None


def execute_sql_query_tool(
    dataset: Dataset,
    sql: str,
    title: Optional[str] = None,
    user_query: str = "",
) -> Dict[str, Any]:
    """
    Executes an arbitrary read-only DuckDB SQL query over `data`, with:
    1. Automatic SQL syntax/column repair on error
    2. Self-reflection recovery when strict text filters return 0 rows
    3. Pre-computed comparative insights (% share, #1 vs #2 lead, spread)
    """
    duck_df = get_duckdb_ready_dataframe(dataset)
    executed_sql = sql
    reflection_notes: List[str] = []

    try:
        res_df = run_duckdb_df(duck_df, executed_sql)
    except Exception as e1:
        reflection_notes.append(f"Initial SQL hit DuckDB error ({str(e1)[:90]}); auto-repaired column/type syntax.")
        repaired = _repair_sql_with_gemma(executed_sql, str(e1), duck_df, user_query)
        if repaired:
            try:
                executed_sql = repaired
                res_df = run_duckdb_df(duck_df, executed_sql)
            except Exception as e2:
                # Try deterministic dynamic SQL builder before giving up!
                col_map = dataset.column_mapping or {}
                dyn = build_dynamic_sql_from_query(
                    user_query,
                    duck_df,
                    col_profiles=col_map.get("_column_profiles"),
                    col_semantics=col_map.get("_column_semantics"),
                )
                if dyn:
                    executed_sql, dyn_title = dyn
                    title = title or dyn_title
                    res_df = run_duckdb_df(duck_df, executed_sql)
                    reflection_notes.append("Recovered via deterministic schema-grounded SQL synthesis.")
                else:
                    return {"error": f"SQL query failed: {e2}"}
        else:
            col_map = dataset.column_mapping or {}
            dyn = build_dynamic_sql_from_query(
                user_query,
                duck_df,
                col_profiles=col_map.get("_column_profiles"),
                col_semantics=col_map.get("_column_semantics"),
            )
            if dyn:
                executed_sql, dyn_title = dyn
                title = title or dyn_title
                try:
                    res_df = run_duckdb_df(duck_df, executed_sql)
                    reflection_notes.append("Recovered via deterministic schema-grounded SQL synthesis.")
                except Exception as e3:
                    return {"error": f"SQL query failed: {e3}"}
            else:
                return {"error": f"SQL query failed: {e1}"}

    # Self-reflection Check: If strict string equality filter returned 0 rows, relax to ILIKE '%...%'!
    if len(res_df) == 0 and "where" in executed_sql.lower():
        relaxed_sql = _relax_sql_equality_to_ilike(executed_sql)
        if relaxed_sql:
            try:
                relaxed_df = run_duckdb_df(duck_df, relaxed_sql)
                if len(relaxed_df) > 0:
                    res_df = relaxed_df
                    executed_sql = relaxed_sql
                    reflection_notes.append(
                        f"Exact filter returned 0 rows; relaxed text match to case-insensitive substring (`ILIKE`) and recovered {len(res_df):,} matching rows."
                    )
            except Exception:
                pass

    total_rows = len(res_df)
    display_df = res_df.head(50)
    headers = list(display_df.columns)
    rows = [[_format_cell(r.get(c)) for c in headers] for r in display_df.to_dicts()]

    chart_data = []
    if 2 <= len(display_df) <= 20 and len(headers) >= 2:
        num_cols = [c for c in headers[1:] if display_df[c].dtype in NUMERIC_DTYPES]
        if num_cols:
            val_col = num_cols[0]
            for r in display_df.head(15).to_dicts():
                v = r.get(val_col)
                if isinstance(v, (int, float)) and not np.isnan(float(v)):
                    chart_data.append({
                        "name": str(r.get(headers[0], ""))[:28],
                        "value": round(float(v), 2),
                    })

    fact_records = []
    for r in display_df.head(15).to_dicts():
        clean_r = {}
        for k, v in r.items():
            if isinstance(v, float) and not np.isnan(v):
                clean_r[k] = round(v, 2)
            elif v is not None and not (isinstance(v, float) and np.isnan(v)):
                clean_r[k] = str(v) if not isinstance(v, (int, float, bool)) else v
        fact_records.append(clean_r)

    computed_insights = _compute_table_insights(display_df)

    facts: Dict[str, Any] = {
        "sql_executed": validate_readonly_sql(executed_sql),
        "total_rows_returned": total_rows,
        "columns": headers,
        "results": fact_records,
    }
    if computed_insights:
        facts["computed_insights"] = computed_insights
    if reflection_notes:
        facts["self_reflection"] = reflection_notes

    return {
        "tool_name": "run_sql_query",
        "title": title or f"SQL Query Results ({total_rows:,} rows)",
        "sql_query": validate_readonly_sql(executed_sql),
        "table_headers": headers,
        "table_rows": rows,
        "total_rows_returned": total_rows,
        "chart_data": chart_data,
        "self_reflection": reflection_notes,
        "facts": facts,
    }


def _export_dataframe_files(
    dataset_id: str,
    df_out: pl.DataFrame,
    slug: str,
) -> Tuple[str, Optional[str], str]:
    """Saves `df_out` to CSV (and XLSX if <=100k rows) in settings.EXPORT_DIR and returns (csv_url, xlsx_url, filename)."""
    safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", slug or "generated_data").strip("_")[:48] or "generated_data"
    short_id = uuid.uuid4().hex[:8]
    csv_fname = f"{dataset_id}_{safe_slug}_{short_id}.csv"
    csv_path = settings.EXPORT_DIR / csv_fname
    df_out.write_csv(csv_path)
    csv_url = f"/api/v1/datasets/{dataset_id}/exports/{csv_fname}"

    xlsx_url = None
    if len(df_out) <= 100_000:
        try:
            xlsx_fname = f"{dataset_id}_{safe_slug}_{short_id}.xlsx"
            xlsx_path = settings.EXPORT_DIR / xlsx_fname
            df_out.to_pandas().to_excel(xlsx_path, index=False)
            xlsx_url = f"/api/v1/datasets/{dataset_id}/exports/{xlsx_fname}"
        except Exception as ex_err:
            logger.warning(f"Excel export skipped: {ex_err}")

    return csv_url, xlsx_url, f"{safe_slug}.csv"


def execute_forecast_future(
    dataset: Dataset,
    db: Session,
    target_metric: Optional[str] = None,
    horizon: Optional[int] = None,
    filter_column: Optional[str] = None,
    filter_value: Optional[str] = None,
) -> Dict[str, Any]:
    """
    On-demand future prediction engine!
    Forecasts any numeric column (or filtered slice) in temporal datasets, OR aggregates precomputed
    ProductForecast series for ERP stock summaries. Always builds a history+forecast chart and CSV export.
    """
    from apps.api.app.services.forecasting.backtest import run_rolling_origin_backtest_for_product
    from apps.api.app.services.forecasting.baselines import _generate_future_dates
    from apps.api.app.services.profiling import calculate_syntetos_boylan

    col_map = dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})
    df = get_dataset_dataframe(dataset)

    # Identify date column in raw df if any
    date_col = col_map.get("date")
    if not date_col or date_col not in df.columns:
        date_col = None
        for c in df.columns:
            if df[c].dtype in (pl.Date, pl.Datetime) or check_date_parse_rate(df, c) >= 0.75:
                date_col = c
                break

    # Resolve target numeric column
    metric_col = resolve_column_name(df.columns, target_metric, col_semantics) if target_metric else None
    if not metric_col or df[metric_col].dtype not in NUMERIC_DTYPES:
        mapped_qty = col_map.get("quantity")
        if mapped_qty and mapped_qty in df.columns and df[mapped_qty].dtype in NUMERIC_DTYPES:
            metric_col = mapped_qty
        else:
            num_cands = [c for c in df.columns if df[c].dtype in NUMERIC_DTYPES]
            metric_col = num_cands[0] if num_cands else None

    # CASE A: Dataset has a genuine time axis -> run live backtested forecast on exact metric & slice!
    if date_col and metric_col:
        work_df = df
        slice_label = f"Overall {metric_col}"
        if filter_column and filter_value is not None and str(filter_value).strip() != "":
            f_col = resolve_column_name(df.columns, filter_column, col_semantics)
            if f_col and f_col in df.columns:
                exact_sub = work_df.filter(pl.col(f_col).cast(pl.Utf8).str.to_lowercase() == str(filter_value).strip().lower())
                sub = exact_sub if len(exact_sub) > 0 else work_df.filter(
                    pl.col(f_col).cast(pl.Utf8).str.to_lowercase().str.contains(str(filter_value).strip().lower(), literal=True)
                )
                if len(sub) > 0:
                    work_df = sub
                    slice_label = f"{metric_col} ({f_col} = {filter_value})"

        date_pref = detect_date_format_ambiguity(work_df, date_col)
        work_df = work_df.with_columns(
            parse_date_column(work_df[date_col], date_pref).alias("__dt__"),
            pl.col(metric_col).cast(pl.Float64, strict=False).alias("__y__"),
        ).filter(pl.col("__dt__").is_not_null() & pl.col("__y__").is_not_null())

        if len(work_df) >= 3:
            use_mean = _is_non_additive_metric(metric_col)
            ts_df = (
                work_df.group_by("__dt__")
                .agg((pl.col("__y__").mean() if use_mean else pl.col("__y__").sum()).alias("quantity"))
                .rename({"__dt__": "date"})
                .sort("date")
            )

            _, freq_code = infer_natural_frequency(ts_df["date"])
            default_horizons = {"D": 14, "W": 8, "M": 6, "Q": 4, "Y": 3}
            eff_horizon = max(1, min(int(horizon or default_horizons.get(freq_code, 12)), 90))

            y_hist = ts_df["quantity"].to_numpy()
            dates_hist = ts_df["date"].to_list()

            if len(ts_df) >= 6:
                gran_map = {"D": "daily", "W": "weekly", "M": "monthly", "Q": "quarterly", "Y": "yearly"}
                s_class, _, _ = calculate_syntetos_boylan(
                    y_hist,
                    granularity=gran_map.get(freq_code, "monthly"),
                    min_periods=6,
                )
                if s_class == "inactive":
                    s_class = "smooth"
                b_res = run_rolling_origin_backtest_for_product(
                    df_product=ts_df,
                    product_name=slice_label,
                    series_class=s_class,
                    horizon=eff_horizon,
                    num_folds=2,
                    include_timesfm=False,
                    freq=freq_code,
                )
            else:
                b_res = None

            if b_res and b_res.final_forecast:
                fc_dates = b_res.final_forecast.dates
                pt = [round(float(v), 2) for v in b_res.final_forecast.point_forecast]
                q10 = [round(float(v), 2) for v in b_res.final_forecast.quantiles.get(0.1, [x * 0.85 for x in pt])]
                q90 = [round(float(v), 2) for v in b_res.final_forecast.quantiles.get(0.9, [x * 1.15 for x in pt])]
                model_used = b_res.best_model_name
                best_wape = next((m.get("wape") for m in b_res.metrics_table if m.get("model_name") == model_used), None)
            else:
                # Linear trend fallback for very short series (3-5 observations)
                x_idx = np.arange(len(y_hist), dtype=float)
                slope, intercept = np.polyfit(x_idx, y_hist, 1) if len(y_hist) >= 2 else (0.0, float(y_hist[-1]))
                fut_idx = np.arange(len(y_hist), len(y_hist) + eff_horizon, dtype=float)
                pt_arr = np.maximum(0.0, intercept + slope * fut_idx)
                resid_std = float(np.std(y_hist - (intercept + slope * x_idx))) if len(y_hist) >= 3 else float(abs(np.mean(y_hist)) * 0.1)
                pt = [round(float(v), 2) for v in pt_arr]
                q10 = [round(float(max(0.0, v - 1.28 * resid_std)), 2) for v in pt_arr]
                q90 = [round(float(v + 1.28 * resid_std), 2) for v in pt_arr]
                fc_dates = _generate_future_dates(dates_hist[-1], eff_horizon, freq_code)
                model_used = "LinearTrend"
                best_wape = None

            # Build combined history + forecast chart series
            forecast_chart = []
            hist_tail_n = min(len(dates_hist), max(24, eff_horizon * 3))
            for d_val, q_val in zip(dates_hist[-hist_tail_n:], y_hist[-hist_tail_n:]):
                forecast_chart.append({
                    "date": d_val.isoformat(),
                    "history": round(float(q_val), 2),
                    "forecast": None,
                    "lower": None,
                    "upper": None,
                })
            # Bridge point so chart line connects smoothly
            if forecast_chart:
                forecast_chart[-1]["forecast"] = forecast_chart[-1]["history"]
            for d_str, p_val, lo_val, hi_val in zip(fc_dates, pt, q10, q90):
                forecast_chart.append({
                    "date": d_str,
                    "history": None,
                    "forecast": p_val,
                    "lower": lo_val,
                    "upper": hi_val,
                })

            # Export forecast table as downloadable CSV & XLSX
            fc_out_df = pl.DataFrame({
                "date": fc_dates,
                "series": [slice_label] * len(fc_dates),
                f"predicted_{metric_col}": pt,
                "lower_80_ci": q10,
                "upper_80_ci": q90,
                "model": [model_used] * len(fc_dates),
            })
            csv_url, xlsx_url, dl_name = _export_dataframe_files(dataset.id, fc_out_df, f"forecast_{metric_col}")

            last_actual = round(float(y_hist[-1]), 2)
            first_pred = pt[0] if pt else 0.0
            last_pred = pt[-1] if pt else 0.0
            avg_pred = round(float(np.mean(pt)), 2) if pt else 0.0
            sum_pred = round(float(np.sum(pt)), 2) if pt else 0.0
            pct_change = round(((last_pred - last_actual) / abs(last_actual)) * 100.0, 2) if abs(last_actual) > 1e-9 else 0.0

            table_rows = [
                [d_str, f"{p_val:,.2f}", f"{lo_val:,.2f} – {hi_val:,.2f}", model_used]
                for d_str, p_val, lo_val, hi_val in zip(fc_dates[:15], pt[:15], q10[:15], q90[:15])
            ]

            facts = {
                "target": slice_label,
                "frequency": freq_code,
                "historical_periods": len(ts_df),
                "last_observed_date": dates_hist[-1].isoformat(),
                "last_observed_value": last_actual,
                "forecast_horizon": eff_horizon,
                "forecast_start_date": fc_dates[0] if fc_dates else None,
                "forecast_end_date": fc_dates[-1] if fc_dates else None,
                "winning_model": model_used,
                "backtest_wape_pct": round(float(best_wape) * 100.0, 2) if best_wape is not None else None,
                "first_predicted_value": first_pred,
                "final_predicted_value": last_pred,
                "average_predicted_value": avg_pred,
                "total_predicted_sum": sum_pred if not use_mean else None,
                "projected_change_pct": pct_change,
                "predictions": [
                    {"date": d_str, "predicted": p_val, "p10": lo_val, "p90": hi_val}
                    for d_str, p_val, lo_val, hi_val in zip(fc_dates[:8], pt[:8], q10[:8], q90[:8])
                ],
            }

            return {
                "tool_name": "forecast_future",
                "title": f"Future Prediction: {slice_label} (Next {eff_horizon} Periods)",
                "model_used": model_used,
                "forecast_chart": forecast_chart,
                "table_headers": ["Future Date", f"Predicted {metric_col}", "80% Confidence Range", "Model"],
                "table_rows": table_rows,
                "csv_url": csv_url,
                "xlsx_url": xlsx_url,
                "filename": dl_name,
                "facts": facts,
            }

    # CASE B: Pre-computed ProductForecasts (e.g. ERP Stock Summary without raw date column)
    forecasts = db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset.id).all()
    if forecasts:
        if filter_value:
            fv = str(filter_value).lower()
            matched_f = [f for f in forecasts if fv in f.product_id.lower()]
            if matched_f:
                forecasts = matched_f

        active_f = [f for f in forecasts if f.point_forecast]
        if active_f:
            sorted_f = sorted(active_f, key=lambda x: sum(x.point_forecast or []), reverse=True)
            fc_dates = sorted_f[0].forecast_dates or []
            h_len = len(fc_dates)
            agg_pt = [round(sum((f.point_forecast[i] if i < len(f.point_forecast) else 0.0) for f in active_f), 2) for i in range(h_len)]
            agg_lo = [round(sum((f.quantiles.get("0.1", f.point_forecast)[i] if i < len(f.point_forecast) else 0.0) for f in active_f), 2) for i in range(h_len)]
            agg_hi = [round(sum((f.quantiles.get("0.9", f.point_forecast)[i] if i < len(f.point_forecast) else 0.0) for f in active_f), 2) for i in range(h_len)]

            forecast_chart = [
                {"date": d_s, "history": None, "forecast": p_v, "lower": lo_v, "upper": hi_v}
                for d_s, p_v, lo_v, hi_v in zip(fc_dates, agg_pt, agg_lo, agg_hi)
            ]

            top_rows = []
            export_records = []
            for f in sorted_f:
                tot = round(float(sum(f.point_forecast or [])), 2)
                p10_s = round(float(sum(f.quantiles.get("0.1", f.point_forecast or []))), 2)
                p90_s = round(float(sum(f.quantiles.get("0.9", f.point_forecast or []))), 2)
                export_records.append({
                    "product": f.product_id,
                    "demand_class": f.series_class,
                    "chosen_model": f.chosen_model,
                    "projected_total_units": tot,
                    "lower_80_ci": p10_s,
                    "upper_80_ci": p90_s,
                })
                if len(top_rows) < 12:
                    top_rows.append([f.product_id, f"{tot:,.1f}", f"{p10_s:,.1f} – {p90_s:,.1f}", f.chosen_model])

            csv_url, xlsx_url, dl_name = _export_dataframe_files(dataset.id, pl.DataFrame(export_records), "catalog_future_forecasts")
            total_catalog = round(float(sum(agg_pt)), 2)

            facts = {
                "products_forecasted": len(active_f),
                "horizon_periods": h_len,
                "total_projected_units": total_catalog,
                "top_projected_items": export_records[:6],
            }
            return {
                "tool_name": "forecast_future",
                "title": f"Future Demand Forecast ({len(active_f)} Items, {h_len}-Day Horizon)",
                "forecast_chart": forecast_chart,
                "table_headers": ["Product / Entity", "Projected Units", "80% Range", "Winning Model"],
                "table_rows": top_rows,
                "csv_url": csv_url,
                "xlsx_url": xlsx_url,
                "filename": dl_name,
                "facts": facts,
            }

    return {
        "error": (
            "This dataset is cross-sectional (it does not contain a date/time sequence column for time-series forecasting). "
            "You can still run any SQL query, correlation, ranking, grouping, or CSV export on all its columns!"
        )
    }


def _generate_sql_for_csv_with_gemma(
    user_query: str,
    df: pl.DataFrame,
) -> Tuple[Optional[str], str]:
    schema_desc = ", ".join(f'"{c}" ({df[c].dtype})' for c in df.columns)
    sample_json = json.dumps(df.head(2).to_dicts(), default=str)
    prompt = (
        "You are an expert DuckDB SQL engineer generating a new CSV dataset from table `data` based on the user's request.\n"
        f"Exact columns in `data`: {schema_desc}\n"
        f"Sample rows: {sample_json}\n"
        f"User request: \"{user_query}\"\n\n"
        "Write a single read-only DuckDB SELECT query against `data` that produces the exact table the user wants exported. "
        "Always wrap column names in double quotes (e.g. \"Sale Qty\"). Do NOT add a tiny LIMIT unless the user asked for top N.\n"
        "Respond ONLY in JSON: {\"sql\": \"SELECT ... FROM data ...\", \"filename_slug\": \"descriptive_name\"}"
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
            )
            if resp.status_code == 200:
                parsed = json.loads(resp.json().get("response", "{}"))
                return parsed.get("sql"), parsed.get("filename_slug") or "generated_export"
    except Exception:
        pass
    return None, "generated_export"


def _heuristic_export_sql(user_query: str, df: pl.DataFrame, col_semantics: Dict[str, str]) -> str:
    """Deterministic fallback SQL builder for CSV generation when LLM is unavailable."""
    q_low = user_query.lower()
    # Check for "top N"
    m_top = re.search(r"\btop\s+(\d+)\b", q_low)
    limit_clause = f" LIMIT {int(m_top.group(1))}" if m_top else ""

    # Check for "by <dim>" grouping
    grp_col = None
    for c in df.columns:
        if df[c].dtype not in NUMERIC_DTYPES and c.lower() in q_low:
            grp_col = c
            break
    if not grp_col and " by " in q_low:
        after_by = q_low.split(" by ", 1)[1].split()[0]
        grp_col = resolve_column_name(df.columns, after_by, col_semantics)

    num_cols = [c for c in df.columns if df[c].dtype in NUMERIC_DTYPES]
    mentioned_nums = [c for c in num_cols if c.lower() in q_low]
    if not mentioned_nums:
        for tok in ("profit", "revenue", "sales", "quantity", "stock", "ma", "price"):
            if tok in q_low:
                resolved = resolve_column_name(df.columns, tok, col_semantics)
                if resolved and resolved in num_cols and resolved not in mentioned_nums:
                    mentioned_nums.append(resolved)
    target_nums = mentioned_nums or num_cols[:4]

    if grp_col and any(w in q_low for w in ("summar", "group", "by ", "total", "average", "avg")):
        agg_fn = "AVG" if any(w in q_low for w in ("average", "avg", "mean")) else "SUM"
        agg_selects = [f'ROUND({agg_fn}("{m}"), 2) AS "{agg_fn.lower()}_{normalize_col_token(m)}"' for m in target_nums[:4]]
        order_expr = f'"{agg_fn.lower()}_{normalize_col_token(target_nums[0])}" DESC' if target_nums else "record_count DESC"
        select_parts = [f'"{grp_col}"', "COUNT(*) AS record_count"] + agg_selects
        return f'SELECT {", ".join(select_parts)} FROM data WHERE "{grp_col}" IS NOT NULL GROUP BY "{grp_col}" ORDER BY {order_expr}{limit_clause}'

    # Row-level filter check: look for literal category value inside query
    where_clauses = []
    for c in df.columns:
        if df[c].dtype in (pl.Utf8, pl.String) and df[c].n_unique() <= 100:
            for val in df[c].drop_nulls().unique().to_list():
                v_str = str(val).strip()
                if len(v_str) >= 3 and re.search(rf"\b{re.escape(v_str.lower())}\b", q_low):
                    safe_v = v_str.replace("'", "''")
                    where_clauses.append(f'LOWER(CAST("{c}" AS VARCHAR)) = \'{safe_v.lower()}\'')
                    break

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sort_sql = f' ORDER BY "{target_nums[0]}" DESC' if target_nums else ""
    return f"SELECT * FROM data{where_sql}{sort_sql}{limit_clause}"


def execute_generate_csv_export(
    dataset: Dataset,
    db: Session,
    sql: Optional[str] = None,
    filename_slug: Optional[str] = None,
    include_forecasts: bool = False,
    description: Optional[str] = None,
    user_query: str = "",
) -> Dict[str, Any]:
    """Generates a downloadable CSV & Excel file from any user transformation, filter, aggregation, or forecast request."""
    if include_forecasts or any(w in user_query.lower() for w in ("forecast", "prediction", "future")):
        if not sql:
            fc_res = execute_forecast_future(dataset=dataset, db=db)
            if not fc_res.get("error") and fc_res.get("csv_url"):
                fc_res["tool_name"] = "generate_csv_export"
                fc_res["title"] = f"Generated Forecast CSV: {fc_res.get('filename', 'forecast.csv')}"
                return fc_res

    duck_df = get_duckdb_ready_dataframe(dataset)
    col_map = dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})

    chosen_sql = sql
    slug = filename_slug or "custom_export"
    if not chosen_sql:
        chosen_sql, gen_slug = _generate_sql_for_csv_with_gemma(user_query, duck_df)
        if not filename_slug and gen_slug:
            slug = gen_slug
    if not chosen_sql:
        chosen_sql = _heuristic_export_sql(user_query, duck_df, col_semantics)

    try:
        out_df = run_duckdb_df(duck_df, chosen_sql)
    except Exception as e1:
        repaired = _repair_sql_with_gemma(chosen_sql, str(e1), duck_df, user_query)
        if repaired:
            try:
                chosen_sql = repaired
                out_df = run_duckdb_df(duck_df, chosen_sql)
            except Exception:
                chosen_sql = _heuristic_export_sql(user_query, duck_df, col_semantics)
                out_df = run_duckdb_df(duck_df, chosen_sql)
        else:
            chosen_sql = _heuristic_export_sql(user_query, duck_df, col_semantics)
            out_df = run_duckdb_df(duck_df, chosen_sql)

    csv_url, xlsx_url, dl_filename = _export_dataframe_files(dataset.id, out_df, slug)

    preview_df = out_df.head(10)
    headers = list(out_df.columns)
    preview_cols = headers[:8]
    rows = [[_format_cell(r.get(c)) for c in preview_cols] for r in preview_df.to_dicts()]

    facts = {
        "generated_filename": dl_filename,
        "rows_exported": len(out_df),
        "columns_exported": len(headers),
        "column_names": headers,
        "sql_transformation": validate_readonly_sql(chosen_sql),
        "sample_rows": preview_df.select(preview_cols).head(4).to_dicts(),
    }

    return {
        "tool_name": "generate_csv_export",
        "title": description or f"Generated CSV Ready ({len(out_df):,} rows × {len(headers)} columns)",
        "filename": dl_filename,
        "csv_url": csv_url,
        "xlsx_url": xlsx_url,
        "row_count": len(out_df),
        "column_count": len(headers),
        "sql_query": validate_readonly_sql(chosen_sql),
        "table_headers": preview_cols,
        "table_rows": rows,
        "facts": facts,
    }


def execute_analyze_correlations(
    df: pl.DataFrame,
    target_column: Optional[str] = None,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Computes Pearson correlations across numeric columns and identifies strongest relationships."""
    num_cols = [c for c in df.columns if df[c].dtype in NUMERIC_DTYPES]
    if len(num_cols) < 2:
        return {"error": "At least 2 numeric columns are required to compute correlations."}

    target = resolve_column_name(num_cols, target_column, col_semantics) if target_column else None
    pairs: List[Dict[str, Any]] = []

    if target and target in num_cols:
        t_arr = df[target].cast(pl.Float64).to_numpy()
        for other in num_cols:
            if other == target:
                continue
            o_arr = df[other].cast(pl.Float64).to_numpy()
            mask = ~np.isnan(t_arr) & ~np.isnan(o_arr)
            if np.sum(mask) >= 3 and np.std(t_arr[mask]) > 0 and np.std(o_arr[mask]) > 0:
                r_val = float(np.corrcoef(t_arr[mask], o_arr[mask])[0, 1])
                if not np.isnan(r_val):
                    pairs.append({"col_a": target, "col_b": other, "correlation": round(r_val, 4)})
    else:
        capped = num_cols[:15]
        for i in range(len(capped)):
            a_arr = df[capped[i]].cast(pl.Float64).to_numpy()
            for j in range(i + 1, len(capped)):
                b_arr = df[capped[j]].cast(pl.Float64).to_numpy()
                mask = ~np.isnan(a_arr) & ~np.isnan(b_arr)
                if np.sum(mask) >= 3 and np.std(a_arr[mask]) > 0 and np.std(b_arr[mask]) > 0:
                    r_val = float(np.corrcoef(a_arr[mask], b_arr[mask])[0, 1])
                    if not np.isnan(r_val):
                        pairs.append({"col_a": capped[i], "col_b": capped[j], "correlation": round(r_val, 4)})

    pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)
    top_pairs = pairs[:10]

    def _strength(r: float) -> str:
        ar = abs(r)
        direction = "Positive" if r >= 0 else "Negative"
        if ar >= 0.7:
            return f"Strong {direction}"
        if ar >= 0.4:
            return f"Moderate {direction}"
        if ar >= 0.2:
            return f"Weak {direction}"
        return "Negligible"

    table_rows = [
        [p["col_a"], p["col_b"], f"{p['correlation']:+.3f}", _strength(p["correlation"])]
        for p in top_pairs
    ]
    chart_data = [
        {"name": f"{p['col_b']}" if target else f"{p['col_a'][:12]}×{p['col_b'][:12]}", "value": p["correlation"]}
        for p in top_pairs[:8]
    ]

    return {
        "tool_name": "analyze_correlations",
        "title": f"Key Correlations & Drivers{f' for {target}' if target else ''}",
        "table_headers": ["Metric A", "Metric B", "Pearson r", "Relationship"],
        "table_rows": table_rows,
        "chart_data": chart_data,
        "facts": {
            "target_column": target,
            "numeric_columns_analyzed": len(num_cols),
            "top_correlations": top_pairs,
        },
    }


def execute_detect_anomalies(
    df: pl.DataFrame,
    column: Optional[str] = None,
    limit: int = 10,
    col_semantics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Detects extreme statistical outlier rows using Z-score & IQR boundaries."""
    num_cols = [c for c in df.columns if df[c].dtype in NUMERIC_DTYPES]
    if not num_cols:
        return {"error": "No numeric columns available for outlier detection."}

    col_name = resolve_column_name(num_cols, column, col_semantics) if column else num_cols[0]
    if not col_name or col_name not in num_cols:
        col_name = num_cols[0]

    limit = max(1, min(int(limit or 10), 20))
    work = df.filter(pl.col(col_name).is_not_null()).with_columns(pl.col(col_name).cast(pl.Float64).alias("__val__"))
    if len(work) < 4:
        return {"error": f"Not enough records in '{col_name}' to detect anomalies."}

    vals = work["__val__"].to_numpy()
    mean = float(np.mean(vals))
    std = float(np.std(vals)) or 1.0
    q25, q75 = np.percentile(vals, [25, 75])
    iqr = max(float(q75 - q25), 1e-9)
    upper_iqr = q75 + 2.5 * iqr
    lower_iqr = q25 - 2.5 * iqr

    flagged = (
        work.with_columns(((pl.col("__val__") - mean).abs() / std).alias("__zscore__"))
        .filter((pl.col("__val__") > upper_iqr) | (pl.col("__val__") < lower_iqr) | (pl.col("__zscore__") > 2.5))
        .sort("__zscore__", descending=True)
    )

    total_anomalies = len(flagged)
    sample = flagged.head(limit)
    label_cols = [c for c in df.columns if c != col_name and df[c].dtype not in NUMERIC_DTYPES][:3]
    if not label_cols:
        label_cols = [c for c in df.columns if c != col_name][:2]
    headers = label_cols + [col_name, "Z-Score"]
    rows = [
        [_format_cell(r.get(c)) for c in label_cols] + [_format_cell(r.get(col_name)), f"{float(r['__zscore__']):.2f}σ"]
        for r in sample.to_dicts()
    ]

    return {
        "tool_name": "detect_anomalies",
        "title": f"Anomaly & Outlier Detection on '{col_name}' ({total_anomalies:,} flagged)",
        "table_headers": headers,
        "table_rows": rows,
        "facts": {
            "column": col_name,
            "mean": round(mean, 2),
            "std": round(std, 2),
            "iqr_bounds": [round(float(lower_iqr), 2), round(float(upper_iqr), 2)],
            "total_anomalies_detected": total_anomalies,
            "top_anomalies": sample.select(label_cols + [col_name]).head(5).to_dicts(),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────

def execute_dataset_tool(
    dataset: Dataset,
    tool_name: str,
    args: Dict[str, Any],
    db: Session,
    user_query: str = "",
) -> Dict[str, Any]:
    """
    Executes a chosen tool on the dataset deterministically with DuckDB, Polars, and statistical models.
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
    if tool_name == "run_sql_query":
        return execute_sql_query_tool(
            dataset=dataset,
            sql=args.get("sql") or "SELECT * FROM data LIMIT 15",
            title=args.get("title"),
            user_query=user_query,
        )
    if tool_name == "forecast_future":
        return execute_forecast_future(
            dataset=dataset,
            db=db,
            target_metric=args.get("target_metric"),
            horizon=args.get("horizon"),
            filter_column=args.get("filter_column"),
            filter_value=args.get("filter_value"),
        )
    if tool_name == "generate_csv_export":
        return execute_generate_csv_export(
            dataset=dataset,
            db=db,
            sql=args.get("sql"),
            filename_slug=args.get("filename_slug"),
            include_forecasts=bool(args.get("include_forecasts", False)),
            description=args.get("description"),
            user_query=user_query,
        )

    # Remaining tools operate directly on the Polars dataframe
    try:
        df = get_dataset_dataframe(dataset)
    except Exception as e:
        return {"error": f"Failed to access dataset: {str(e)}"}

    # Dynamic schema-derived defaults (zero hardcoded retail column assumptions)
    default_dim = (
        col_map.get("product")
        if col_map.get("product") in df.columns
        else next((c for c in df.columns if df[c].dtype not in NUMERIC_DTYPES and df[c].dtype not in (pl.Date, pl.Datetime)), df.columns[0])
    )
    default_metric = (
        col_map.get("quantity")
        if col_map.get("quantity") in df.columns
        else next((c for c in df.columns if df[c].dtype in NUMERIC_DTYPES), df.columns[-1])
    )

    if tool_name == "analyze_correlations":
        return execute_analyze_correlations(
            df=df,
            target_column=args.get("target_column"),
            col_semantics=col_semantics,
        )
    elif tool_name == "detect_anomalies":
        return execute_detect_anomalies(
            df=df,
            column=args.get("column"),
            limit=args.get("limit", 10),
            col_semantics=col_semantics,
        )
    elif tool_name == "rank_top_bottom":
        return execute_rank_top_bottom(
            df=df,
            dimension=args.get("dimension") or default_dim,
            metric=args.get("metric") or default_metric,
            n=args.get("n", 5),
            ascending=bool(args.get("ascending", False)),
            col_semantics=col_semantics,
        )
    elif tool_name == "filter_and_aggregate":
        return execute_filter_and_aggregate(
            df=df,
            target_metric=args.get("target_metric") or default_metric,
            agg_func=args.get("agg_func") or ("mean" if _is_non_additive_metric(default_metric) else "sum"),
            filter_column=args.get("filter_column"),
            filter_value=args.get("filter_value"),
            group_by=args.get("group_by"),
            col_semantics=col_semantics,
        )
    elif tool_name == "column_distribution_stats":
        return execute_column_distribution_stats(
            df=df,
            column=args.get("column") or default_metric,
            col_semantics=col_semantics,
        )
    elif tool_name == "compare_groups":
        return execute_compare_groups(
            df=df,
            dimension=args.get("dimension") or default_dim,
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
# AGENTIC TOOL SELECTION (Full-Schema + Sample-Aware Gemma AI Data Analyst)
# ─────────────────────────────────────────────────────────────────────────────

def _build_schema_context_for_agent(
    available_columns: List[str],
    col_semantics: Dict[str, str],
    col_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    lines = []
    for c in available_columns[:60]:
        prof = (col_profiles or {}).get(c, {})
        dtype = prof.get("polars_dtype") or prof.get("role") or "unknown"
        role = prof.get("role", "")
        if role == "temporal":
            detail = f"Date ({prof.get('date_min')}..{prof.get('date_max')}, {prof.get('frequency')})"
        elif role in ("numeric_measure", "numeric_attribute"):
            detail = f"{dtype} [min={prof.get('min')}, max={prof.get('max')}, avg={prof.get('mean')}]"
        elif prof.get("top_values"):
            examples = ", ".join(str(v["value"]) for v in prof["top_values"][:4])
            detail = f"{dtype} ({prof.get('unique_count')} unique; e.g. {examples})"
        else:
            detail = f"{dtype} — {col_semantics.get(c, '')[:70]}"
        lines.append(f'- "{c}": {detail}')
    return "\n".join(lines)


async def plan_analysis_with_cot(
    dataset: Dataset,
    query: str,
    available_columns: List[str],
    col_semantics: Dict[str, str],
    col_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    recent_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, str]], Optional[str]]:
    """
    Chain-of-Thought (CoT) Analytical Planner!
    1. Dynamically grounds user terms & actual cell values against ANY dataset's schema.
    2. Prompts Gemma to generate step-by-step `reasoning_steps` BEFORE emitting `tool` & `arguments`
       (and optional `secondary_sql` for baseline/context enrichment).
    3. Falls back to a dynamic schema-driven CoT + DuckDB SQL synthesizer when offline.
    Returns: (tool_name, arguments, cot_steps, secondary_sql)
    """
    query_lower = query.lower().strip()
    col_map = dataset.column_mapping or {}
    row_grain = (col_map.get("_row_grain") or {}).get("description", f"{len(available_columns)} columns")

    try:
        duck_df = get_duckdb_ready_dataframe(dataset)
        grounded = discover_query_context(query, duck_df, col_profiles, col_semantics)
    except Exception:
        duck_df = None
        grounded = {
            "mentioned_measures": [],
            "mentioned_dimensions": [],
            "mentioned_temporals": [],
            "matched_value_filters": [],
            "numeric_predicates": [],
        }

    csv_export_intent = (
        any(ext in query_lower for ext in ("csv", "excel", "xlsx", "spreadsheet"))
        and any(v in query_lower for v in ("generate", "create", "export", "download", "make", "give", "save", "extract", "build", "prepare", "output", "produce"))
    )
    forecast_intent = any(
        kw in query_lower
        for kw in (
            "future prediction", "predict", "prediction", "predictions", "forecast",
            "next month", "next quarter", "next year", "next week", "future trend",
            "project future", "upcoming demand", "what will happen", "future sales",
            "show me the future",
        )
    ) and not csv_export_intent

    schema_block = _build_schema_context_for_agent(available_columns, col_semantics, col_profiles)
    history_block = ""
    if recent_history:
        turns_str = "\n".join(f"{m['role'].upper()}: {m['content'][:220]}" for m in recent_history[-4:])
        history_block = f"\nRECENT CONVERSATION CONTEXT:\n{turns_str}\n"

    grounding_hint_parts = [f"Dataset Grain: {row_grain}"]
    if grounded["mentioned_measures"]:
        m_notes = [
            f'"{m}" ({"non-additive -> prefer AVG" if _is_non_additive_metric(m) else "additive -> SUM or AVG"})'
            for m in grounded["mentioned_measures"]
        ]
        grounding_hint_parts.append(f"Matched numeric columns: {', '.join(m_notes)}")
    if grounded["mentioned_dimensions"]:
        grounding_hint_parts.append(f"Matched dimension columns: {grounded['mentioned_dimensions']}")
    if grounded["matched_value_filters"]:
        grounding_hint_parts.append(f"Detected exact cell values in data: {grounded['matched_value_filters']}")
    if grounded["numeric_predicates"]:
        grounding_hint_parts.append(f"Detected numeric predicates: {grounded['numeric_predicates']}")
    grounding_hint = "\n".join(grounding_hint_parts)

    cot_steps: List[Dict[str, str]] = [
        {
            "phase": "Schema & Value Grounding",
            "thought": grounding_hint.replace("\n", " • "),
        }
    ]

    system_prompt = (
        "You are a Principal AI Data Analyst capable of analyzing ANY dataset from ANY industry or domain "
        "(finance, healthcare, SaaS, HR, real estate, IoT, retail, operations, science) via DuckDB SQL and statistical models.\n"
        "You MUST think step-by-step using Chain-of-Thought (`reasoning_steps`) BEFORE selecting the tool and arguments.\n\n"
        "CHAIN-OF-THOUGHT PROTOCOL (write `reasoning_steps` FIRST in JSON):\n"
        "- Step 1 (Intent): Identify the exact analytical question, metric(s), dimensions, time horizon, or file transformation requested.\n"
        "- Step 2 (Column & Math Logic): Map user terms to exact column names in `data`. Decide whether each metric is additive (`SUM`) or a rate/price/average/score (`AVG`). Guard division with `NULLIF(x, 0)`.\n"
        "- Step 3 (Execution Plan): Formulate the precise DuckDB SQL (`run_sql_query` / `generate_csv_export`) or statistical tool (`forecast_future`, `analyze_correlations`, `detect_anomalies`, etc.).\n\n"
        "AVAILABLE TOOLS:\n"
        "1. run_sql_query: Preferred for ANY custom, multi-condition, time-trend (yearly/quarterly/monthly), grouped, ratio, percentage, HAVING, window-function, DISTINCT, or comparative query on `data`.\n"
        "   Args: {\"sql\": \"SELECT ... FROM data ...\", \"title\": \"Short result title\"}\n"
        "   SQL RULES: Table is ALWAYS `data`. Wrap EVERY column name in double quotes (e.g. \"MA\", \"Sale Qty\"). Date columns are native DATE type.\n"
        "2. forecast_future: Future time-series predictions with 80% confidence bands.\n"
        "   Args: {\"target_metric\": str|null, \"horizon\": int|null, \"filter_column\": str|null, \"filter_value\": str|null}\n"
        "3. generate_csv_export: Generate & export a new downloadable CSV/Excel file from a transformation/filter/summary.\n"
        "   Args: {\"sql\": \"SELECT ... FROM data ...\", \"filename_slug\": \"short_name\", \"include_forecasts\": bool, \"description\": \"What is in this CSV\"}\n"
        "4. analyze_correlations: Pearson correlations & variable relationships. Args: {\"target_column\": str|null}\n"
        "5. detect_anomalies: Z-score & IQR outlier rows. Args: {\"column\": str|null, \"limit\": int}\n"
        "6. rank_top_bottom: Top/bottom N ranking of 1 dimension by 1 metric. Args: {\"dimension\": str, \"metric\": str, \"n\": int, \"ascending\": bool}\n"
        "7. filter_and_aggregate: Simple sum/mean/min/max/count of 1 metric. Args: {\"target_metric\": str, \"agg_func\": str, \"filter_column\": str|null, \"filter_value\": str|null, \"group_by\": str|null}\n"
        "8. column_distribution_stats: Mean, median, std, p25, p75, min, max of 1 column. Args: {\"column\": str}\n"
        "9. compare_groups: Compare 2-5 entities in 1 dimension. Args: {\"dimension\": str, \"entities\": [str, ...]}\n"
        "10. inspect_raw_sample: View raw matching rows. Args: {\"search_column\": str|null, \"keyword\": str|null, \"limit\": int}\n"
        "11. inventory_status_lookup: Reorder points & stockout risk (only if inventory forecasts exist). Args: {\"product_query\": str|null, \"risk_filter\": str|null}\n\n"
        f"DATASET SCHEMA (`data` table, {len(available_columns)} columns):\n{schema_block}\n\n"
        f"DYNAMIC GROUNDING HINTS:\n{grounding_hint}\n"
        f"{history_block}\n"
        "Respond ONLY with valid JSON in this exact field order:\n"
        "{\n"
        "  \"reasoning_steps\": [\"Step 1: ...\", \"Step 2: ...\", \"Step 3: ...\"],\n"
        "  \"tool\": \"tool_name\" | null,\n"
        "  \"arguments\": {...},\n"
        "  \"secondary_sql\": \"Optional second DuckDB SELECT query for baseline/total context\" | null\n"
        "}"
    )

    user_prompt = f"User Request: \"{query}\"\nGenerate Chain-of-Thought JSON plan:"
    valid_tool_names = {t["name"] for t in TOOL_DEFINITIONS}

    try:
        async with httpx.AsyncClient(timeout=14.0) as client:
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
                raw = resp.json().get("response", "").strip()
                parsed = json.loads(raw)
                llm_thoughts = parsed.get("reasoning_steps") or []
                if isinstance(llm_thoughts, list):
                    for idx, step_txt in enumerate(llm_thoughts[:4]):
                        if step_txt and str(step_txt).strip():
                            cot_steps.append({
                                "phase": f"CoT Step {idx + 1}",
                                "thought": str(step_txt).strip(),
                            })
                tool = parsed.get("tool")
                args = parsed.get("arguments") or {}
                sec_sql = parsed.get("secondary_sql")
                if isinstance(sec_sql, str) and not sec_sql.strip():
                    sec_sql = None

                if csv_export_intent and tool != "generate_csv_export":
                    sql_candidate = args.get("sql") if tool == "run_sql_query" else None
                    return (
                        "generate_csv_export",
                        {
                            "sql": sql_candidate,
                            "filename_slug": args.get("filename_slug") or "generated_data",
                            "include_forecasts": any(w in query_lower for w in ("forecast", "predict", "future")),
                            "description": f"Generated CSV for: {query[:60]}",
                        },
                        cot_steps,
                        None,
                    )
                if tool and tool in valid_tool_names:
                    return tool, args, cot_steps, sec_sql
    except Exception as e:
        logger.warning(f"Gemma CoT planning fallback triggered: {e}")

    # ── Dynamic Schema-Driven CoT Planner (100% dataset-agnostic, works offline!) ──
    if csv_export_intent:
        dyn_sql = None
        if duck_df is not None and not any(w in query_lower for w in ("forecast", "predict", "future")):
            dyn = build_dynamic_sql_from_query(query, duck_df, col_profiles, col_semantics)
            if dyn:
                dyn_sql = dyn[0]
        cot_steps.append({
            "phase": "CoT Analytical Plan",
            "thought": f"Detected file-export intent; synthesizing DuckDB transformation SQL ({dyn_sql or 'auto-export'}) to materialize CSV & Excel files.",
        })
        return (
            "generate_csv_export",
            {
                "sql": dyn_sql,
                "filename_slug": "custom_export",
                "include_forecasts": any(w in query_lower for w in ("forecast", "predict", "future")),
                "description": f"Generated CSV for: {query[:60]}",
            },
            cot_steps,
            None,
        )

    if forecast_intent:
        m_h = re.search(r"\bnext\s+(\d+)\b", query_lower)
        h_val = int(m_h.group(1)) if m_h else None
        target_m = grounded["mentioned_measures"][0] if grounded["mentioned_measures"] else None
        filter_c = grounded["matched_value_filters"][0]["column"] if grounded["matched_value_filters"] else None
        filter_v = grounded["matched_value_filters"][0]["values"][0] if grounded["matched_value_filters"] else None
        cot_steps.append({
            "phase": "CoT Forecast Plan",
            "thought": f"Selected time-series forecasting on metric `{target_m or col_map.get('quantity')}`"
            + (f" filtered by `{filter_c} = {filter_v}`" if filter_c else " across full series")
            + " with rolling-origin backtest model competition and 80% prediction intervals.",
        })
        return (
            "forecast_future",
            {
                "target_metric": target_m,
                "horizon": h_val,
                "filter_column": filter_c,
                "filter_value": filter_v,
            },
            cot_steps,
            None,
        )

    if any(w in query_lower for w in ("correlat", "relationship", "driver", "impact on", "associated with")):
        target_c = grounded["mentioned_measures"][0] if grounded["mentioned_measures"] else None
        cot_steps.append({
            "phase": "CoT Statistical Plan",
            "thought": f"Computing pairwise Pearson correlation matrix across numeric features{f' anchored on `{target_c}`' if target_c else ''}.",
        })
        return "analyze_correlations", {"target_column": target_c}, cot_steps, None

    if any(w in query_lower for w in ("anomal", "outlier", "unusual", "spike", "extreme")):
        target_c = grounded["mentioned_measures"][0] if grounded["mentioned_measures"] else None
        cot_steps.append({
            "phase": "CoT Anomaly Plan",
            "thought": f"Scanning `{target_c or 'primary numeric column'}` using dual 2.5×IQR and |Z| > 2.5σ thresholds to isolate extreme records.",
        })
        return "detect_anomalies", {"column": target_c, "limit": 10}, cot_steps, None

    if any(w in query_lower for w in ("distribution", "summary statistics", "median", "std", "percentile", "quartil")):
        target_c = (
            grounded["mentioned_measures"][0]
            if grounded["mentioned_measures"]
            else (grounded["mentioned_dimensions"][0] if grounded["mentioned_dimensions"] else (available_columns[0] if available_columns else ""))
        )
        cot_steps.append({
            "phase": "CoT Distribution Plan",
            "thought": f"Computing full parametric and non-parametric distribution statistics (mean, median, std, IQR) for `{target_c}`.",
        })
        return "column_distribution_stats", {"column": target_c}, cot_steps, None

    # Complex / multi-condition / time-bucketed / multi-dimension queries -> Dynamic DuckDB SQL!
    has_complex_sql_signals = (
        len(grounded["matched_value_filters"]) >= 1
        or len(grounded["numeric_predicates"]) >= 1
        or len(grounded["mentioned_dimensions"]) >= 2
        or any(w in query_lower for w in ("by year", "yearly", "by quarter", "quarterly", "by month", "monthly", "over time", "trend", "between", "above", "below", "greater than", "less than", "more than", "under "))
    )
    if duck_df is not None and has_complex_sql_signals and not ("compare" in query_lower and len(grounded["numeric_predicates"]) == 0 and len(grounded["matched_value_filters"]) <= 1):
        dyn = build_dynamic_sql_from_query(query, duck_df, col_profiles, col_semantics)
        if dyn:
            dyn_sql, dyn_title = dyn
            cot_steps.append({
                "phase": "CoT SQL Formulation",
                "thought": f"Synthesized multi-clause DuckDB query (`{dyn_sql}`) respecting column types and aggregation semantics.",
            })
            return "run_sql_query", {"sql": dyn_sql, "title": dyn_title}, cot_steps, None

    if "compare" in query_lower or " vs " in query_lower or " versus " in query_lower:
        dim = (
            grounded["matched_value_filters"][0]["column"]
            if grounded["matched_value_filters"]
            else (grounded["mentioned_dimensions"][0] if grounded["mentioned_dimensions"] else (available_columns[0] if available_columns else ""))
        )
        ents = grounded["matched_value_filters"][0]["values"] if grounded["matched_value_filters"] else []
        cot_steps.append({
            "phase": "CoT Comparison Plan",
            "thought": f"Comparing entities {ents or 'top groups'} across dimension `{dim}` on all numeric measures.",
        })
        return "compare_groups", {"dimension": dim, "entities": ents}, cot_steps, None

    if duck_df is not None:
        dyn = build_dynamic_sql_from_query(query, duck_df, col_profiles, col_semantics)
        if dyn:
            dyn_sql, dyn_title = dyn
            cot_steps.append({
                "phase": "CoT SQL Formulation",
                "thought": f"Formulated schema-grounded DuckDB query: `{dyn_sql}`.",
            })
            return "run_sql_query", {"sql": dyn_sql, "title": dyn_title}, cot_steps, None

    if any(w in query_lower for w in ("show rows", "sample rows", "raw rows", "records where", "list all", "find rows", "preview")):
        cot_steps.append({
            "phase": "CoT Row Inspection",
            "thought": "Retrieving direct sample records from the underlying dataset.",
        })
        return "inspect_raw_sample", {"limit": 8}, cot_steps, None

    return None, {}, cot_steps, None


async def select_tool_with_gemma(
    query: str,
    available_columns: List[str],
    col_semantics: Dict[str, str],
    col_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    recent_history: Optional[List[Dict[str, str]]] = None,
    dataset: Optional[Dataset] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Backward-compatible wrapper around `plan_analysis_with_cot`."""
    if dataset is not None:
        tool, args, _, _ = await plan_analysis_with_cot(
            dataset=dataset,
            query=query,
            available_columns=available_columns,
            col_semantics=col_semantics,
            col_profiles=col_profiles,
            recent_history=recent_history,
        )
        return (tool, args) if tool else None

    # Lightweight dummy dataset wrapper when called standalone without Dataset ORM instance
    dummy_ds = Dataset(
        id="adhoc",
        name="adhoc",
        original_filename="adhoc.csv",
        file_path="",
        column_mapping={"_column_semantics": col_semantics, "_column_profiles": col_profiles or {}},
    )
    tool, args, _, _ = await plan_analysis_with_cot(
        dataset=dummy_ds,
        query=query,
        available_columns=available_columns,
        col_semantics=col_semantics,
        col_profiles=col_profiles,
        recent_history=recent_history,
    )
    return (tool, args) if tool else None

