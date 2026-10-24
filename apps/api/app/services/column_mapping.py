import re
from typing import Any, Dict, List, Optional, Tuple

import polars as pl

LOGICAL_FIELDS = {
    "date": {
        "required": True,
        "keywords": [
            "date",
            "order_date",
            "sales_date",
            "invoice_date",
            "trans_date",
            "transaction_date",
            "time",
            "timestamp",
            "day",
            "dt",
        ],
        "avoid": [
            "closing", "opening", "inward", "outward", "qty", "quantity",
            "value", "sale", "rate", "brand", "vendor", "size", "mrp", "cost", "disc"
        ],
    },
    "product": {
        "required": True,
        "keywords": [
            "brand",
            "department",
            "product",
            "sku",
            "item",
            "item_code",
            "product_sku",
            "product_id",
            "product_name",
            "description",
            "style",
            "article",
            "barcode",
            "material",
        ],
        "avoid": [
            "qty", "quantity", "value", "rate", "amount", "price", "sale",
            "profit", "closing", "opening", "inward", "outward", "disc", "date", "time"
        ],
    },
    "quantity": {
        "required": True,
        "keywords": [
            "net_sale_qty",
            "net_sales_qty",
            "sale_qty",
            "sales_qty",
            "units_sold",
            "units",
            "quantity",
            "qty",
            "volume",
            "sold_qty",
            "amount_sold",
            "net_qty",
        ],
        "avoid": [
            "inward", "outward", "closing", "opening", "value", "rate", "price", "profit", "disc", "amount"
        ],
    },
    "revenue": {
        "required": False,
        "keywords": [
            "gross_item_sale",
            "gross_sales",
            "sale_value",
            "total_sales",
            "revenue",
            "turnover",
            "sales_amount",
            "net_sales",
            "amount",
        ],
        "avoid": [
            "inward", "outward", "closing", "opening", "qty", "quantity"
        ],
    },
    "stock_on_hand": {
        "required": False,
        "keywords": [
            "closing_qty",
            "closing_stock",
            "closing",
            "stock_on_hand",
            "current_stock",
            "soh",
            "qty_on_hand",
            "avail_stock",
            "inventory",
            "stock",
        ],
        "avoid": [
            "sale", "inward", "outward", "sold", "rate", "value", "profit"
        ],
    },
    "profit": {
        "required": False,
        "keywords": [
            "profit",
            "profit_amt",
            "net_profit",
            "gross_profit",
            "profit_margin",
            "margin",
            "earnings",
            "gain",
        ],
        "avoid": ["qty", "quantity", "date", "time", "rate_pct", "percent", "pct"],
    },
    "cost": {
        "required": False,
        "keywords": [
            "cost",
            "cogs",
            "cost_of_goods",
            "purchase_price",
            "cost_price",
            "landed_cost",
            "unit_cost",
        ],
        "avoid": ["qty", "quantity", "date", "time"],
    },
    "discount": {
        "required": False,
        "keywords": [
            "discount",
            "disc",
            "discount_amt",
            "discount_amount",
            "markdown",
            "rebate",
        ],
        "avoid": ["qty", "quantity", "date", "time", "flag", "pct"],
    },
    "vendor": {
        "required": False,
        "keywords": [
            "vendor",
            "supplier",
            "party",
            "manufacturer",
            "dealer",
        ],
        "avoid": ["qty", "value", "sale", "date"],
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
        "avoid": ["qty", "sale", "date"],
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
        "avoid": ["qty", "sale", "date"],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# COMPREHENSIVE COLUMN SEMANTICS DICTIONARY
# Keys are normalized substrings (lowercase, non-alphanumeric → '_').
# explain_columns() matches each column name against these keys via substring.
# This covers financial, inventory, classification, and performance columns
# for ANY business dataset — not just a single ERP format.
# ─────────────────────────────────────────────────────────────────────────────
COLUMN_SEMANTICS_DICTIONARY = {
    # ── Supplier / Classification ──────────────────────────────────────────
    "vendor":              "Supplier or distributor providing the merchandise",
    "supplier":            "Supplier or distributor providing the merchandise",
    "manufacturer":        "Brand manufacturer / OEM producing the goods",
    "party":               "Supplier or trading partner name",
    "dealer":              "Authorized dealer or distributor",

    # ── Product / Item Classification ──────────────────────────────────────
    "main_category":       "Top-level merchandise category (e.g. Ladies, Mens, Kids, Electronics)",
    "category":            "Product category or classification group",
    "section":             "Merchandise section or sub-department within the store",
    "department":          "Product style or department (e.g. Casual T-Shirt, Jeans, Shirts)",
    "division":            "Business division or major product group",
    "segment":             "Customer or product market segment",
    "brand":               "Manufacturer brand label attached to the item",
    "product":             "Product name or SKU identifier",
    "item":                "Individual stock-keeping unit or item description",
    "sku":                 "Stock Keeping Unit — unique product identifier code",
    "barcode":             "Barcode or EAN/UPC product scan code",
    "article":             "Article number — product style or variant code",
    "style":               "Product style code or design reference",
    "description":         "Text description of the product or service",
    "size":                "Item sizing attribute (e.g. S, M, L, XL, 28, 30, 32)",
    "color":               "Product colour or colour code",
    "uom":                 "Unit of measure (e.g. Pcs, Kg, Box, Dozen)",
    "mrp":                 "Maximum Retail Price — consumer-facing sticker price set by the manufacturer",
    "hsn":                 "HSN / SAC code for GST tax classification",
    "gstin":               "GSTIN — Goods and Services Tax Identification Number",

    # ── Inventory Movement ─────────────────────────────────────────────────
    "opening_qty":         "Opening inventory units at the start of the reporting period",
    "opening":             "Opening inventory units at the start of the reporting period",
    "op_qty":              "Opening inventory quantity at period start",
    "closing_qty":         "Current unsold inventory units on hand in the store/warehouse",
    "closing_stock":       "Current unsold inventory units on hand",
    "closing":             "Closing (on-hand) inventory units at end of the reporting period",
    "stock_on_hand":       "Physical inventory units available for sale right now",
    "soh":                 "Stock On Hand — physical units available for immediate sale",
    "net_inward_qty":      "New inventory units received from suppliers (GRN) during the period",
    "inward":              "Goods received / inward stock movement — units added to inventory",
    "net_outward_qty":     "Units transferred out, returned to vendors, or adjusted out of inventory",
    "outward":             "Goods dispatched / outward stock movement — units leaving inventory",
    "transfer":            "Inter-branch or inter-warehouse stock transfer quantity",
    "receipt":             "Goods receipt quantity — units arriving from supplier",
    "adjustment":          "Inventory adjustment or write-off quantity",
    "shrinkage":           "Inventory shrinkage or loss due to damage, theft, or expiry",

    # ── Sales Quantities ───────────────────────────────────────────────────
    "net_sale_qty":        "Net customer sales units (Gross Sales minus Customer Returns)",
    "net_sales_qty":       "Net customer sales units after returns",
    "sale_qty":            "Gross units sold to customers during the period",
    "sales_qty":           "Gross units sold to customers during the period",
    "sold_qty":            "Total units sold to end customers",
    "units_sold":          "Number of individual product units sold to customers",
    "sale_rtn_qty":        "Customer return units — items sent back by buyers",
    "return_qty":          "Customer or supplier return units during the period",
    "returned":            "Quantity returned by customers or to vendors",

    # ── Revenue / Sales Value ──────────────────────────────────────────────
    "gross_item_sale":     "Total gross revenue earned from sales before any deductions",
    "gross_sales":         "Total gross sales value before returns or discounts",
    "gross_sale":          "Total gross sales value before returns or discounts",
    "sale_value":          "Monetary value of items sold during the period",
    "sales_value":         "Total monetary value of sales transactions",
    "net_sales":           "Net sales revenue after deducting returns and discounts",
    "net_revenue":         "Revenue remaining after all deductions — the real top-line income",
    "revenue":             "Sales revenue generated from customer transactions",
    "turnover":            "Total sales turnover / revenue for the period",
    "sales_amount":        "Total monetary amount billed to customers",
    "net_amount":          "Net billed amount after discounts and taxes",
    "gross_amount":        "Total billed amount before deductions",

    # ── Pricing ────────────────────────────────────────────────────────────
    "selling_price":       "Price at which the item is sold to end customers",
    "sale_rate":           "Per-unit sale price or selling rate applied",
    "unit_price":          "Price per individual unit sold",
    "avg_price":           "Average realized selling price per unit",
    "avg_rate":            "Average rate per unit across transactions",
    "purchase_price":      "Per-unit cost paid to the supplier when procuring stock",
    "cost_price":          "Per-unit purchase / landed cost of the item",
    "landed_cost":         "Total per-unit cost including freight, duties, and handling",

    # ── Stock Valuation ────────────────────────────────────────────────────
    "cl_value_in_rate":    "Closing stock value at wholesale purchase cost (In-Rate / Cost basis)",
    "cl_value_net_rate":   "Closing stock value at net selling rate",
    "cl_value_mrp_rate":   "Closing stock value at Maximum Retail Price (MRP basis)",
    "stock_value":         "Total monetary value of current inventory on hand",
    "inventory_value":     "Monetary value of inventory based on cost or selling price",
    "op_value":            "Opening stock monetary value at period start",

    # ── Profit & Margin ────────────────────────────────────────────────────
    "profit_amt":          "Absolute profit earned on sales in currency (Revenue minus Cost)",
    "profit_amount":       "Absolute profit amount in currency terms",
    "net_profit":          "Net profit after all costs, returns, and overheads are deducted",
    "gross_profit":        "Gross profit — revenue minus direct cost of goods sold (COGS)",
    "profit_margin":       "Profit margin amount or percentage relative to revenue",
    "profit_pct":          "Profit percentage — gross profit as a % of selling price or cost",
    "profit_percent":      "Profit as a percentage of revenue or cost",
    "profit_amt_in_rate":  "Profit earned on sales over the purchase (in-rate / cost) value",
    "profit":              "Profit earned on the sales transactions in this period",
    "margin":              "Gross margin — the spread between selling price and purchase cost",
    "gross_margin":        "Gross margin percentage or amount (selling price minus COGS)",
    "earnings":            "Net earnings or profit from sales activity",
    "markup":              "Price markup applied over the purchase cost to set the selling price",

    # ── Cost of Goods ──────────────────────────────────────────────────────
    "cogs":                "Cost of Goods Sold — direct cost of inventory sold during the period",
    "cost_of_goods":       "Total purchase cost of the items sold (COGS)",
    "cost":                "Total cost incurred for acquiring goods sold",
    "purchase_cost":       "Total cost paid to suppliers for goods received",

    # ── Discounts & Deductions ─────────────────────────────────────────────
    "discount_amt":        "Total discount amount deducted from the sale price",
    "discount_amount":     "Monetary value of price reductions or promotional discounts given",
    "discount":            "Discount applied to the sale — either amount or percentage",
    "disc":                "Discount applied to the sale",
    "markdown":            "Price markdown applied to reduce unsold or ageing inventory",
    "rebate":              "Supplier or customer rebate amount",

    # ── Tax ────────────────────────────────────────────────────────────────
    "tax":                 "Tax amount (e.g. GST, VAT) charged on the sale",
    "gst":                 "GST (Goods and Services Tax) levied on the transaction",
    "vat":                 "Value Added Tax on the transaction",
    "cgst":                "Central GST component of the total GST charged",
    "sgst":                "State GST component of the total GST charged",
    "igst":                "Integrated GST charged on inter-state supply",

    # ── Performance / KPIs ────────────────────────────────────────────────
    "offtake_by_qty":      "Sell-through rate: percentage of opening stock sold during the period",
    "offtake_pct":         "Offtake percentage — proportion of available inventory that was sold",
    "sell_through":        "Sell-through rate — units sold as a fraction of units available",
    "sell_through_rate":   "Sell-through rate percentage (sold / available x 100)",
    "days_of_supply":      "Number of days current inventory will last at the current sales rate",
    "days_cover":          "Days of inventory cover — how many days before stockout at current velocity",
    "stock_cover":         "Inventory cover in days based on current run rate",
    "velocity":            "Sales velocity — average daily or weekly units sold",
    "stock_turn":          "Inventory turnover — how many times stock is sold and replaced per year",
    "turnover_rate":       "Inventory turnover rate — times stock turns over in a period",
    "conversion":          "Sales conversion rate — transactions converted from footfall or visits",
}


def explain_columns(columns: List[str]) -> Dict[str, str]:
    """Generates human-readable plain English definitions for every column in the dataset.
    Tries to find the most specific (longest) matching key from COLUMN_SEMANTICS_DICTIONARY."""
    # Sort dictionary keys by length descending so the most specific key wins
    sorted_keys = sorted(COLUMN_SEMANTICS_DICTIONARY.keys(), key=len, reverse=True)
    # Percentage/ratio keys tried first when the column name contains '%' or ends with 'pct'
    pct_priority_keys = [k for k in sorted_keys if k.endswith(("_pct", "_percent", "_ratio"))]
    explanations: Dict[str, str] = {}
    for c in columns:
        norm = re.sub(r"[^a-z0-9]+", "_", c.strip().lower()).strip("_")
        matched = None
        # If column looks like a percentage/ratio column, try pct-specific keys first
        is_pct_col = "%" in c or norm.endswith(("_pct", "_percent", "_rate", "_ratio", "pct"))
        candidate_keys = (pct_priority_keys + sorted_keys) if is_pct_col else sorted_keys
        for k in candidate_keys:
            if k in norm or norm in k:
                matched = COLUMN_SEMANTICS_DICTIONARY[k]
                break
        if not matched:
            matched = f"Column containing {c.replace('_', ' ')} records"
        explanations[c] = matched
    return explanations



def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.strip().lower()).strip("_")


def check_date_parse_rate(df: pl.DataFrame, col: str, max_samples: int = 100) -> float:
    """Dynamically estimate the percentage of values in a column that can be parsed as dates."""
    sample_series = df[col].drop_nulls().head(max_samples)
    if len(sample_series) == 0:
        return 0.0

    # If already Date or Datetime dtype
    if sample_series.dtype in (pl.Date, pl.Datetime):
        return 1.0

    import pandas as pd

    try:
        p_s = sample_series.to_pandas()
        if pd.api.types.is_numeric_dtype(p_s):
            max_val = p_s.max()
            if max_val is not None and (max_val > 1e8 or (20000 <= max_val <= 80000)):
                return 1.0
            return 0.0

        parsed = pd.to_datetime(p_s, errors="coerce", format="mixed")
        valid = parsed.notnull().sum()
        return float(valid) / len(p_s)
    except Exception:
        return 0.0


def suggest_column_mapping_heuristics(
    df: pl.DataFrame,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, str], Dict[str, float], Dict[str, str]]:
    """
    Heuristically map raw dataframe columns to logical schema fields.
    Supports both:
    1. Transaction time series (with individual date timestamps)
    2. Stock summary reports (aggregated inventory reports with Closing Qty and Net Sale Qty)
    """
    suggested_mapping: Dict[str, str] = {}
    confidence: Dict[str, float] = {}
    reasons: Dict[str, str] = {}

    norm_col_map = {col: _normalize_name(col) for col in df.columns}
    used_columns = set()

    # Check if there is any genuine date column in the table
    date_candidates: List[Tuple[str, float]] = []
    for col in df.columns:
        norm = norm_col_map[col]
        if any(av in norm for av in LOGICAL_FIELDS["date"]["avoid"]):
            continue
        if df[col].dtype in (pl.Date, pl.Datetime):
            date_candidates.append((col, 1.0))
            continue
        rate = check_date_parse_rate(df, col)
        if rate > 0.4:
            date_candidates.append((col, rate))

    # Check if dataset has stock on hand and sales metrics (Stock Summary mode)
    stock_col = None
    for col, norm in norm_col_map.items():
        if any(kw == norm or kw in norm for kw in LOGICAL_FIELDS["stock_on_hand"]["keywords"]):
            if not any(av in norm for av in LOGICAL_FIELDS["stock_on_hand"]["avoid"]):
                stock_col = col
                break

    sale_col = None
    for col, norm in norm_col_map.items():
        if any(kw == norm or kw in norm for kw in LOGICAL_FIELDS["quantity"]["keywords"]):
            if not any(av in norm for av in LOGICAL_FIELDS["quantity"]["avoid"]):
                sale_col = col
                break

    is_stock_summary = (not date_candidates) and (stock_col is not None or (meta and "stock" in str(meta).lower()))

    if is_stock_summary:
        # Stock Summary Mode
        suggested_mapping["_dataset_type"] = "stock_summary"
        suggested_mapping["date"] = "__period__"
        confidence["date"] = 0.95
        reasons["date"] = "Inferred from period metadata (Inventory Stock Summary mode)"

        if stock_col:
            suggested_mapping["stock_on_hand"] = stock_col
            confidence["stock_on_hand"] = 0.95
            reasons["stock_on_hand"] = f"Closing on-hand inventory matched column '{stock_col}'"
            used_columns.add(stock_col)

        if sale_col:
            suggested_mapping["quantity"] = sale_col
            confidence["quantity"] = 0.95
            reasons["quantity"] = f"Period sales volume matched column '{sale_col}'"
            used_columns.add(sale_col)

        # Map product: prioritize BRAND, then DEPARTMENT, then item/product
        for col, norm in norm_col_map.items():
            if norm in ("brand", "product", "item", "sku", "department", "article"):
                suggested_mapping["product"] = col
                confidence["product"] = 0.90
                reasons["product"] = f"Categorical merchandise column matched '{col}'"
                used_columns.add(col)
                break

        # Map revenue: Gross Item Sale or Sale Value
        for col, norm in norm_col_map.items():
            if col not in used_columns and any(kw in norm for kw in ("gross_item_sale", "gross_sales", "sale_value", "revenue")):
                suggested_mapping["revenue"] = col
                confidence["revenue"] = 0.85
                reasons["revenue"] = f"Sales value matched column '{col}'"
                used_columns.add(col)
                break

        # Map vendor
        for col, norm in norm_col_map.items():
            if col not in used_columns and any(kw in norm for kw in ("vendor", "supplier", "party")):
                suggested_mapping["vendor"] = col
                confidence["vendor"] = 0.85
                reasons["vendor"] = f"Supplier vendor matched column '{col}'"
                used_columns.add(col)
                break

    else:
        # Standard Time Series Mode
        suggested_mapping["_dataset_type"] = "time_series"
        # Score each (field, column) pair
        scores: Dict[str, List[Tuple[str, float, str]]] = {field: [] for field in LOGICAL_FIELDS}

        for col in df.columns:
            norm = norm_col_map[col]
            is_numeric = df[col].dtype in (
                pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                pl.Float32, pl.Float64,
            )
            is_temporal = df[col].dtype in (pl.Date, pl.Datetime)
            date_rate = check_date_parse_rate(df, col) if not is_numeric else 0.0

            for field, cfg in LOGICAL_FIELDS.items():
                score = 0.0
                reason_parts = []

                if any(av in norm for av in cfg.get("avoid", [])):
                    continue

                if norm in cfg["keywords"]:
                    score += 0.70
                    reason_parts.append("Exact column name match")
                elif any(kw in norm for kw in cfg["keywords"]):
                    score += 0.40
                    reason_parts.append("Partial column name match")

                if field == "date":
                    if is_temporal:
                        score += 0.35
                        reason_parts.append("Date/Datetime type")
                    elif date_rate > 0.8:
                        score += 0.35
                        reason_parts.append(f"High date format match ({int(date_rate * 100)}%)")
                elif field == "quantity":
                    if is_numeric:
                        score += 0.25
                        reason_parts.append("Numeric type")
                elif field == "product":
                    if not is_numeric and not is_temporal and date_rate < 0.2:
                        score += 0.20
                        reason_parts.append("Textual identifier column")
                elif field == "revenue":
                    if is_numeric:
                        score += 0.25
                        reason_parts.append("Numeric type")

                if score > 0.3:
                    scores[field].append((col, min(round(score, 2), 1.0), "; ".join(reason_parts)))

        # Assign best matches
        priority_order = ["date", "quantity", "product", "revenue", "stock_on_hand", "vendor", "store", "promo_flag"]
        for field in priority_order:
            candidates = sorted(scores[field], key=lambda x: x[1], reverse=True)
            for col, score, reason in candidates:
                if col not in used_columns:
                    suggested_mapping[field] = col
                    confidence[field] = score
                    reasons[field] = reason
                    used_columns.add(col)
                    break

    # Guaranteed fallback for product and quantity
    if "product" not in suggested_mapping:
        for col in df.columns:
            if col not in used_columns and df[col].dtype in (pl.Utf8, pl.Categorical):
                suggested_mapping["product"] = col
                confidence["product"] = 0.60
                reasons["product"] = "Selected textual column as product"
                used_columns.add(col)
                break

    if "quantity" not in suggested_mapping:
        for col in df.columns:
            if col not in used_columns and df[col].dtype in (
                pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                pl.Float32, pl.Float64
            ):
                suggested_mapping["quantity"] = col
                confidence["quantity"] = 0.60
                reasons["quantity"] = "Selected numeric column as quantity"
                used_columns.add(col)
                break

    # Attach semantics and metadata
    suggested_mapping["_column_semantics"] = explain_columns(df.columns)
    if meta and meta.get("date_range"):
        suggested_mapping["_date_range"] = meta.get("date_range")

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


