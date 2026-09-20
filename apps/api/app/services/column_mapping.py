import re
from typing import Dict, List, Tuple

import polars as pl

LOGICAL_FIELDS = {
    "date": {
        "required": True,
        "keywords": [
            "date",
            "order_date",
            "sales_date",
            "invoice_date",
            "day",
            "timestamp",
            "dt",
            "trans_date",
            "transaction_date",
            "time",
            "period",
        ],
    },
    "product": {
        "required": True,
        "keywords": [
            "product",
            "sku",
            "item",
            "product_id",
            "product_name",
            "item_id",
            "item_name",
            "product_code",
            "article",
            "barcode",
            "material",
        ],
    },
    "quantity": {
        "required": True,
        "keywords": [
            "quantity",
            "qty",
            "units",
            "units_sold",
            "sales_qty",
            "volume",
            "sold",
            "count",
            "amount_sold",
            "net_qty",
        ],
    },
    "revenue": {
        "required": False,
        "keywords": [
            "revenue",
            "sales",
            "total_sales",
            "sales_amount",
            "price",
            "turnover",
            "net_sales",
            "gross_sales",
            "amount",
            "value",
        ],
    },
    "store": {
        "required": False,
        "keywords": [
            "store",
            "location",
            "branch",
            "store_id",
            "outlet",
            "shop",
            "site",
            "channel",
            "warehouse",
        ],
    },
    "promo_flag": {
        "required": False,
        "keywords": [
            "promo",
            "promo_flag",
            "promotion",
            "is_promo",
            "on_promo",
            "discount",
            "promoted",
            "discount_flag",
            "event",
        ],
    },
    "stock_on_hand": {
        "required": False,
        "keywords": [
            "stock",
            "stock_on_hand",
            "inventory",
            "current_stock",
            "soh",
            "on_hand",
            "qty_on_hand",
            "avail_stock",
        ],
    },
}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.strip().lower()).strip("_")


def check_date_parse_rate(df: pl.DataFrame, col: str, max_samples: int = 100) -> float:
    """Estimate the percentage of values in a column that can be parsed as dates."""
    sample_series = df[col].drop_nulls().head(max_samples)
    if len(sample_series) == 0:
        return 0.0

    # If already Date or Datetime dtype
    if sample_series.dtype in (pl.Date, pl.Datetime):
        return 1.0

    parsed_count = 0
    date_patterns = [
        r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}",  # 2023-01-01 or 2023/01/01
        r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}",  # 01-01-2023 or 01/01/2023
        r"^\d{4}\d{2}\d{2}$",  # 20230101
    ]
    str_vals = [str(x).strip() for x in sample_series.to_list()]
    for val in str_vals:
        if any(re.match(pat, val) for pat in date_patterns):
            parsed_count += 1

    return parsed_count / len(str_vals)


def suggest_column_mapping_heuristics(
    df: pl.DataFrame,
) -> Tuple[Dict[str, str], Dict[str, float], Dict[str, str]]:
    """
    Heuristically map raw dataframe columns to logical schema fields.
    Returns:
        suggested_mapping: Dict[logical_field, source_col]
        confidence: Dict[logical_field, float (0.0 - 1.0)]
        reasons: Dict[logical_field, str]
    """
    suggested_mapping: Dict[str, str] = {}
    confidence: Dict[str, float] = {}
    reasons: Dict[str, str] = {}

    norm_col_map = {col: _normalize_name(col) for col in df.columns}
    used_columns = set()

    # Score each (field, column) pair
    scores: Dict[str, List[Tuple[str, float, str]]] = {field: [] for field in LOGICAL_FIELDS}

    for col in df.columns:
        norm_col = norm_col_map[col]
        is_numeric = df[col].dtype in (
            pl.Int8,
            pl.Int16,
            pl.Int32,
            pl.Int64,
            pl.UInt8,
            pl.UInt16,
            pl.UInt32,
            pl.UInt64,
            pl.Float32,
            pl.Float64,
        )
        is_temporal = df[col].dtype in (pl.Date, pl.Datetime)
        date_rate = check_date_parse_rate(df, col) if not is_numeric else 0.0

        for field, meta in LOGICAL_FIELDS.items():
            score = 0.0
            reason_parts = []

            # Exact keyword match
            if norm_col in meta["keywords"]:
                score += 0.70
                reason_parts.append("Exact column name match")
            # Substring match
            elif any(kw in norm_col for kw in meta["keywords"]):
                score += 0.40
                reason_parts.append("Partial column name match")

            # Field specific type hints
            if field == "date":
                if is_temporal:
                    score += 0.35
                    reason_parts.append("Date/Datetime type")
                elif date_rate > 0.8:
                    score += 0.35
                    reason_parts.append(f"High date format match ({int(date_rate * 100)}%)")
                elif is_numeric:
                    score -= 0.5  # Pure numeric is unlikely to be date

            elif field == "quantity":
                if is_numeric:
                    score += 0.25
                    reason_parts.append("Numeric type")
                else:
                    score -= 0.4  # Non-numeric unlikely to be quantity

            elif field == "product":
                if not is_numeric and not is_temporal and date_rate < 0.2:
                    score += 0.20
                    reason_parts.append("Textual/identifier column")

            elif field == "revenue":
                if is_numeric:
                    score += 0.25
                    reason_parts.append("Numeric type")
                else:
                    score -= 0.3

            elif field == "promo_flag":
                # Check for boolean or 0/1 indicator
                unique_vals = set(df[col].drop_nulls().head(50).to_list())
                if unique_vals.issubset(
                    {0, 1, True, False, "0", "1", "true", "false", "Y", "N", "yes", "no"}
                ):
                    score += 0.30
                    reason_parts.append("Binary indicator values")

            if score > 0.3:
                scores[field].append((col, min(round(score, 2), 1.0), "; ".join(reason_parts)))

    # Assign best matches
    # Prioritize required fields first: date, product, quantity
    priority_order = [
        "date",
        "quantity",
        "product",
        "revenue",
        "promo_flag",
        "stock_on_hand",
        "store",
    ]

    for field in priority_order:
        candidates = sorted(scores[field], key=lambda x: x[1], reverse=True)
        for col, score, reason in candidates:
            if col not in used_columns:
                suggested_mapping[field] = col
                confidence[field] = score
                reasons[field] = reason
                used_columns.add(col)
                break

    # If any required field is still missing, try Gemma LLM inference
    required = ["date", "product", "quantity"]
    if any(r not in suggested_mapping for r in required):
        try:
            ai_mapping = infer_column_mapping_with_gemma(df)
            for k, v in ai_mapping.items():
                if k not in suggested_mapping and v in df.columns and v not in used_columns:
                    suggested_mapping[k] = v
                    confidence[k] = 0.85
                    reasons[k] = "Inferred by local Gemma LLM from sample rows"
                    used_columns.add(v)
        except Exception:
            pass

    # Deterministic fallback guarantee: ensure required fields are never left unmapped
    if "date" not in suggested_mapping:
        # Find column with best date parse rate
        best_date_col = None
        best_rate = 0.0
        for col in df.columns:
            if col not in used_columns:
                rate = check_date_parse_rate(df, col)
                if rate > best_rate:
                    best_rate = rate
                    best_date_col = col
        if best_date_col and best_rate > 0.2:
            suggested_mapping["date"] = best_date_col
            confidence["date"] = 0.60
            reasons["date"] = f"Fallback: Best date pattern match ({int(best_rate * 100)}%)"
            used_columns.add(best_date_col)
        elif df.columns:
            # Fallback to first column
            first_col = [c for c in df.columns if c not in used_columns]
            if first_col:
                suggested_mapping["date"] = first_col[0]
                confidence["date"] = 0.40
                reasons["date"] = "Fallback: Selected first available column as date"
                used_columns.add(first_col[0])

    if "quantity" not in suggested_mapping:
        # Pick first numeric column
        for col in df.columns:
            if col not in used_columns:
                is_num = df[col].dtype in (
                    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                    pl.Float32, pl.Float64
                )
                if is_num:
                    suggested_mapping["quantity"] = col
                    confidence["quantity"] = 0.65
                    reasons["quantity"] = "Fallback: Selected first available numeric column as quantity"
                    used_columns.add(col)
                    break

    if "product" not in suggested_mapping:
        # Pick first string or categorical column
        for col in df.columns:
            if col not in used_columns:
                is_str = df[col].dtype in (pl.Utf8, pl.Categorical)
                if is_str:
                    suggested_mapping["product"] = col
                    confidence["product"] = 0.60
                    reasons["product"] = "Fallback: Selected textual column as product identifier"
                    used_columns.add(col)
                    break
        # If still missing, pick any remaining unused column
        if "product" not in suggested_mapping:
            remaining = [c for c in df.columns if c not in used_columns]
            if remaining:
                suggested_mapping["product"] = remaining[0]
                confidence["product"] = 0.40
                reasons["product"] = "Fallback: Assigned remaining column as product identifier"
                used_columns.add(remaining[0])

    return suggested_mapping, confidence, reasons


def infer_column_mapping_with_gemma(df: pl.DataFrame) -> Dict[str, str]:
    """Uses local Gemma LLM to infer column mapping from column names and sample rows."""
    import json
    import httpx
    from apps.api.app.config import settings

    cols = df.columns
    sample_rows = df.head(3).to_dicts()

    prompt = (
        f"You are a sales dataset ingestion parser. Identify which spreadsheet column corresponds to:\n"
        f"- 'date': date of sale or order\n"
        f"- 'product': product name, SKU, item code, or description\n"
        f"- 'quantity': units sold, quantity, or volume\n"
        f"- 'revenue': total price, sales amount, or revenue (optional)\n\n"
        f"Columns available: {cols}\n"
        f"Sample data rows: {json.dumps(sample_rows, default=str)}\n\n"
        f"Reply ONLY with a valid JSON object matching: {{\"date\": \"col_name\", \"product\": \"col_name\", \"quantity\": \"col_name\", \"revenue\": \"col_name\"}}\n"
        f"Do not include any other markdown or text."
    )

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data.get("response", "").strip()
                parsed = json.loads(raw_text)
                return {k: v for k, v in parsed.items() if v in cols}
    except Exception:
        pass

    return {}


