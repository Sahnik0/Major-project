import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

LOGICAL_FIELDS = {
    "date": {
        "required": True,
        "keywords": [
            "date",
            "saledate",
            "sale_date",
            "order_date",
            "sales_date",
            "invoice_date",
            "trans_date",
            "transaction_date",
            "ship_date",
            "posting_date",
            "created_at",
            "updated_at",
            "period",
            "month",
            "quarter",
            "year_month",
            "timestamp",
            "datetime",
            "time",
            "day",
            "dt",
        ],
        "avoid": [
            "closing", "opening", "inward", "outward", "qty", "quantity",
            "brand", "vendor", "size", "mrp", "cost", "disc", "lead_time", "days_of"
        ],
    },
    "product": {
        "required": True,
        "keywords": [
            "product_sku",
            "product_id",
            "product_name",
            "product",
            "sku",
            "item_code",
            "item_name",
            "item",
            "brand",
            "department",
            "category",
            "type",
            "segment",
            "symbol",
            "ticker",
            "series",
            "entity",
            "region",
            "description",
            "style",
            "article",
            "barcode",
            "material",
        ],
        "avoid": [
            "qty", "quantity", "value", "rate", "amount", "price",
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
            "demand",
            "sales",
            "target",
            "value",
            "ma",
            "close",
            "price",
            "total",
            "count",
        ],
        "avoid": [
            "inward", "outward", "closing", "opening", "disc",
            "date", "time", "id", "code", "flag", "promo"
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

    # ── Temporal & General Business / Analytics ────────────────────────────
    "saledate":            "Date of sale or reporting period timestamp",
    "sale_date":           "Date of sale or transaction",
    "order_date":          "Date when the customer order was placed",
    "invoice_date":        "Billing or invoice issue date",
    "transaction_date":    "Date when the transaction occurred",
    "date":                "Calendar date or reporting period timestamp",
    "timestamp":           "Exact date and time of the event or record",
    "period":              "Reporting time period (month, quarter, or fiscal cycle)",
    "quarter":             "Calendar or fiscal quarter",
    "year":                "Calendar or fiscal year",
    "month":               "Calendar month",
    "ma":                  "Moving average or smoothed metric value across periods",
    "moving_average":      "Moving average metric smoothing short-term fluctuations",
    "bedrooms":            "Number of bedrooms (categorical/integer property attribute)",
    "bathrooms":           "Number of bathrooms",
    "property_type":       "Property classification (e.g. house, unit, apartment)",
    "customer_id":         "Unique identifier for each customer",
    "customer":            "Customer name or segment",
    "region":              "Geographic region, territory, or market area",
    "country":             "Country name or ISO country code",
    "state":               "State or province",
    "city":                "City or municipality",
    "channel":             "Sales or marketing channel (e.g. Online, Retail, Wholesale)",
    "store_id":            "Store or branch identifier",
    "store":               "Store, outlet, or location identifier",
    "promo_flag":          "Promotional indicator (1 = on promotion, 0 = regular)",
    "status":              "Current lifecycle or operational status of the record",
    "salary":              "Compensation or salary amount",
    "age":                 "Age metric or tenure value",
    "score":               "Numeric rating, index, or evaluation score",
    "rating":              "Customer or quality rating score",
}


def _match_semantic_dictionary(col: str) -> Optional[str]:
    """Matches a column name against COLUMN_SEMANTICS_DICTIONARY using strict token boundaries
    (preventing short tokens like 'MA' from matching inside 'main_category')."""
    norm = re.sub(r"[^a-z0-9]+", "_", col.strip().lower()).strip("_")
    if not norm:
        return None

    if norm in COLUMN_SEMANTICS_DICTIONARY:
        return COLUMN_SEMANTICS_DICTIONARY[norm]

    norm_tokens = [t for t in norm.split("_") if t]
    sorted_keys = sorted(COLUMN_SEMANTICS_DICTIONARY.keys(), key=len, reverse=True)
    is_pct_col = "%" in col or norm.endswith(("_pct", "_percent", "_rate", "_ratio", "pct"))
    pct_priority_keys = [k for k in sorted_keys if k.endswith(("_pct", "_percent", "_ratio"))]
    candidate_keys = (pct_priority_keys + sorted_keys) if is_pct_col else sorted_keys

    for k in candidate_keys:
        k_tokens = [t for t in k.split("_") if t]
        # Contiguous token sequence match (e.g. ['profit', 'amt'] in ['profit', 'amt', 'in', 'rate'])
        if len(k_tokens) <= len(norm_tokens):
            for start in range(len(norm_tokens) - len(k_tokens) + 1):
                if norm_tokens[start : start + len(k_tokens)] == k_tokens:
                    return COLUMN_SEMANTICS_DICTIONARY[k]
        # Multi-token subset match when key has >=2 meaningful tokens (e.g. sale + qty in sale_adj_qty)
        if len(k_tokens) >= 2 and all(t in norm_tokens for t in k_tokens):
            return COLUMN_SEMANTICS_DICTIONARY[k]

    return None


def explain_columns(columns: List[str], df: Optional[pl.DataFrame] = None) -> Dict[str, str]:
    """Generates accurate, data-grounded plain-English definitions for every column in the dataset."""
    explanations: Dict[str, str] = {}
    profiles = build_column_profiles(df) if df is not None and len(df) > 0 else {}

    for c in columns:
        base_desc = _match_semantic_dictionary(c)
        prof = profiles.get(c)

        if prof:
            role = prof.get("role")
            if role == "temporal":
                span = f"{prof.get('date_min')} to {prof.get('date_max')}" if prof.get("date_min") else "dates"
                freq = prof.get("frequency") or "temporal"
                stats_suffix = f" ({freq}, {span}, {prof.get('unique_count', 0):,} unique dates)"
                explanations[c] = (base_desc + stats_suffix) if base_desc else f"Date/time column{stats_suffix}"
            elif role in ("numeric_measure", "numeric_attribute"):
                min_v = prof.get("min")
                max_v = prof.get("max")
                mean_v = prof.get("mean")
                rng = (
                    f"range {min_v:,.2f} to {max_v:,.2f}, avg {mean_v:,.2f}"
                    if isinstance(min_v, (int, float)) and isinstance(max_v, (int, float))
                    else "numeric values"
                )
                if base_desc:
                    explanations[c] = f"{base_desc} ({rng})"
                elif role == "numeric_attribute":
                    explanations[c] = f"Discrete numeric attribute ({prof.get('unique_count', 0)} distinct values, {rng})"
                else:
                    explanations[c] = f"Numeric business metric ({rng})"
            elif role in ("categorical", "identifier", "boolean", "text"):
                u_cnt = prof.get("unique_count", 0)
                top_vals = [str(v["value"]) for v in (prof.get("top_values") or [])[:4]]
                examples = f" — e.g. {', '.join(top_vals)}" if top_vals else ""
                if base_desc:
                    explanations[c] = f"{base_desc} ({u_cnt:,} unique values{examples})"
                elif role == "identifier":
                    explanations[c] = f"Unique/high-cardinality identifier column ({u_cnt:,} unique values{examples})"
                else:
                    explanations[c] = f"Categorical / text dimension ({u_cnt:,} unique values{examples})"
            else:
                explanations[c] = base_desc or f"Business data column '{c}'"
        else:
            explanations[c] = base_desc or f"Business attribute or metric '{c.replace('_', ' ')}'"

    return explanations



NUMERIC_DTYPES = (
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


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.strip().lower()).strip("_")


def check_date_parse_rate(df: pl.DataFrame, col: str, max_samples: int = 100) -> float:
    """Dynamically estimate the percentage of values in a column that can be parsed as dates."""
    sample_series = df[col].drop_nulls().head(max_samples)
    if len(sample_series) == 0:
        return 0.0

    if sample_series.dtype in (pl.Date, pl.Datetime):
        return 1.0

    # Numeric columns should only be treated as dates if column name strongly suggests a date/timestamp
    norm = _normalize_name(col)
    date_name_hint = any(
        tok in norm.split("_") or tok in norm
        for tok in ("date", "dt", "timestamp", "datetime", "saledate", "time")
    )
    if sample_series.dtype in NUMERIC_DTYPES and not date_name_hint:
        return 0.0

    import pandas as pd

    try:
        p_s = sample_series.to_pandas()
        if pd.api.types.is_numeric_dtype(p_s):
            max_val = p_s.max()
            min_val = p_s.min()
            if min_val is not None and max_val is not None and (min_val > 1e8 or (25000 <= min_val and max_val <= 70000)):
                return 1.0
            return 0.0

        # Require strings to contain digits and look plausible (not pure small floats like "12.5")
        str_vals = [str(v).strip() for v in sample_series.to_list()]
        plausible = [
            s for s in str_vals
            if len(s) >= 6 and any(ch.isdigit() for ch in s) and not re.match(r"^-?\d+(?:\.\d+)?$", s)
        ]
        if len(plausible) / len(str_vals) < 0.5:
            return 0.0

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(pd.Series(str_vals), errors="coerce", format="mixed", dayfirst=True)
        valid = int(parsed.notnull().sum())
        return float(valid) / len(str_vals)
    except Exception:
        return 0.0


def _infer_frequency_label_from_dates(dates_sorted: List[Any]) -> str:
    if len(dates_sorted) < 2:
        return "single-period"
    deltas = []
    for i in range(len(dates_sorted) - 1):
        try:
            diff = (dates_sorted[i + 1] - dates_sorted[i]).days
            if diff > 0:
                deltas.append(diff)
        except Exception:
            continue
    if not deltas:
        return "irregular"
    med = float(np.median(deltas))
    if med <= 2:
        return "daily"
    if 5 <= med <= 10:
        return "weekly"
    if 25 <= med <= 35:
        return "monthly"
    if 80 <= med <= 100:
        return "quarterly"
    if 350 <= med <= 380:
        return "yearly"
    return f"every ~{int(round(med))} days"


def build_column_profiles(df: pl.DataFrame) -> Dict[str, Dict[str, Any]]:
    """
    Computes deep statistical and semantic profiles for EVERY column in a DataFrame
    so the AI immediately understands all rows, columns, types, ranges, and categories.
    """
    total_rows = len(df)
    profiles: Dict[str, Dict[str, Any]] = {}
    if total_rows == 0:
        return profiles

    import warnings

    import pandas as pd

    for col in df.columns:
        s = df[col]
        dtype = s.dtype
        null_cnt = int(s.null_count())
        non_null_cnt = total_rows - null_cnt
        null_pct = round((null_cnt / total_rows) * 100.0, 2) if total_rows > 0 else 0.0
        unique_cnt = int(s.drop_nulls().n_unique()) if non_null_cnt > 0 else 0
        norm = _normalize_name(col)

        prof: Dict[str, Any] = {
            "name": col,
            "polars_dtype": str(dtype),
            "non_null_count": non_null_cnt,
            "null_count": null_cnt,
            "null_pct": null_pct,
            "unique_count": unique_cnt,
        }

        is_bool = dtype == pl.Boolean
        is_num = dtype in NUMERIC_DTYPES
        is_temp = dtype in (pl.Date, pl.Datetime)
        date_rate = 1.0 if is_temp else (check_date_parse_rate(df, col) if not is_num and not is_bool else 0.0)

        if is_temp or date_rate >= 0.75:
            prof["role"] = "temporal"
            try:
                if is_temp:
                    dt_series = s.cast(pl.Date).drop_nulls().unique().sort().to_list()
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        parsed = pd.to_datetime(
                            s.drop_nulls().head(5000).to_pandas(),
                            errors="coerce",
                            format="mixed",
                            dayfirst=True,
                            utc=True,
                        ).dropna()
                    dt_series = sorted({d.date() for d in parsed})
                if dt_series:
                    prof["date_min"] = dt_series[0].isoformat()
                    prof["date_max"] = dt_series[-1].isoformat()
                    prof["frequency"] = _infer_frequency_label_from_dates(dt_series)
                    prof["sample_values"] = [d.isoformat() for d in dt_series[:4]]
            except Exception:
                prof["sample_values"] = [str(x) for x in s.drop_nulls().head(4).to_list()]

        elif is_bool:
            prof["role"] = "boolean"
            vc = s.drop_nulls().value_counts(sort=True).head(4).to_dicts()
            prof["top_values"] = [{"value": str(r[col]), "count": int(r["count"])} for r in vc]

        elif is_num:
            vals = s.drop_nulls().cast(pl.Float64).to_numpy()
            is_int_dtype = dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
            attr_kw = ("bedroom", "bathroom", "room", "flag", "promo", "year", "month", "quarter", "week", "day", "tier", "grade", "id", "code")
            if is_int_dtype and unique_cnt <= 15 and (total_rows >= 20 or any(k in norm for k in attr_kw)):
                prof["role"] = "numeric_attribute"
            else:
                prof["role"] = "numeric_measure"

            if len(vals) > 0:
                prof["sum"] = round(float(np.sum(vals)), 2)
                prof["mean"] = round(float(np.mean(vals)), 2)
                prof["median"] = round(float(np.median(vals)), 2)
                prof["std"] = round(float(np.std(vals)), 2)
                prof["min"] = round(float(np.min(vals)), 2)
                prof["max"] = round(float(np.max(vals)), 2)
                prof["zeros_count"] = int(np.sum(vals == 0))
                prof["negatives_count"] = int(np.sum(vals < 0))
                prof["sample_values"] = [round(float(x), 2) for x in vals[:4]]
            if prof["role"] == "numeric_attribute":
                vc = s.drop_nulls().value_counts(sort=True).head(6).to_dicts()
                prof["top_values"] = [{"value": str(r[col]), "count": int(r["count"])} for r in vc]

        else:
            # Categorical / Identifier / Free text
            if non_null_cnt > 0 and unique_cnt == non_null_cnt and total_rows >= 10:
                prof["role"] = "identifier"
            elif unique_cnt <= max(200, int(total_rows * 0.5)):
                prof["role"] = "categorical"
            else:
                prof["role"] = "text"

            vc = s.drop_nulls().value_counts(sort=True).head(6).to_dicts()
            prof["top_values"] = [{"value": str(r[col]), "count": int(r["count"])} for r in vc]
            prof["sample_values"] = [str(x) for x in s.drop_nulls().head(4).to_list()]

        profiles[col] = prof

    return profiles


def detect_row_grain(
    df: pl.DataFrame,
    profiles: Dict[str, Dict[str, Any]],
    date_col: Optional[str] = None,
) -> Dict[str, Any]:
    """Determines what each individual row in the dataset represents (primary key / composite grain)."""
    n_rows = len(df)
    if n_rows == 0:
        return {"grain_columns": [], "is_unique": False, "description": "Empty dataset"}

    # 1. Single identifier column?
    for col, p in profiles.items():
        if p.get("role") == "identifier" and p.get("unique_count") == n_rows:
            return {
                "grain_columns": [col],
                "is_unique": True,
                "description": f"Each of the {n_rows:,} rows is uniquely identified by `{col}`.",
            }

    # 2. Composite grain across temporal + categorical / discrete attribute columns
    candidate_dims = []
    if date_col and date_col in df.columns:
        candidate_dims.append(date_col)
    for col, p in profiles.items():
        if col == date_col:
            continue
        if p.get("role") in ("categorical", "numeric_attribute") and 1 < p.get("unique_count", 0) <= max(2000, n_rows // 2 + 2):
            candidate_dims.append(col)

    chosen_grain: List[str] = []
    for col in candidate_dims[:5]:
        test_cols = chosen_grain + [col]
        u = int(df.select(test_cols).n_unique())
        prev_u = int(df.select(chosen_grain).n_unique()) if chosen_grain else 1
        if u > prev_u:
            chosen_grain.append(col)
        if u >= int(n_rows * 0.98):
            return {
                "grain_columns": chosen_grain,
                "is_unique": u == n_rows,
                "description": (
                    f"Each row represents a record per {', '.join(f'`{c}`' for c in chosen_grain)} "
                    f"({u:,} distinct combinations across {n_rows:,} rows)."
                ),
            }

    if chosen_grain:
        u = int(df.select(chosen_grain).n_unique())
        return {
            "grain_columns": chosen_grain,
            "is_unique": False,
            "description": (
                f"Rows are organized across {', '.join(f'`{c}`' for c in chosen_grain)} "
                f"({n_rows:,} total rows, {u:,} distinct dimension combinations)."
            ),
        }

    return {
        "grain_columns": df.columns[: min(3, len(df.columns))],
        "is_unique": False,
        "description": f"Tabular dataset containing {n_rows:,} rows across {len(df.columns)} columns.",
    }


def generate_suggested_questions(
    df: pl.DataFrame,
    profiles: Dict[str, Dict[str, Any]],
    mapping: Dict[str, Any],
) -> List[str]:
    """Generates smart, dataset-aware starter prompts using the ACTUAL columns and values in df."""
    dtype_mode = mapping.get("_dataset_type", "tabular")
    date_col = mapping.get("date")
    prod_col = mapping.get("product")
    qty_col = mapping.get("quantity")

    measures = [c for c, p in profiles.items() if p.get("role") == "numeric_measure"]
    cats = [c for c, p in profiles.items() if p.get("role") in ("categorical", "numeric_attribute") and p.get("unique_count", 0) >= 2]

    primary_metric = qty_col if qty_col in df.columns else (measures[0] if measures else df.columns[-1])
    secondary_metric = next((m for m in measures if m != primary_metric), primary_metric)
    primary_cat = prod_col if prod_col in df.columns else (cats[0] if cats else None)

    suggestions: List[str] = []

    if dtype_mode in ("time_series", "stock_summary") or (date_col and date_col in df.columns):
        suggestions.append(f"Show me the future prediction for {primary_metric}")

    if primary_cat and primary_cat in profiles:
        u_cnt = profiles[primary_cat].get("unique_count", 0)
        top_vals = [v["value"] for v in (profiles[primary_cat].get("top_values") or []) if v.get("value")]
        if u_cnt >= 3:
            suggestions.append(f"Top 5 {primary_cat} by {primary_metric}")
        if len(top_vals) >= 2:
            suggestions.append(f"Compare {top_vals[0]} vs {top_vals[1]} in {primary_cat}")
        else:
            suggestions.append(f"Breakdown of {primary_metric} by {primary_cat}")

    if len(cats) >= 2:
        other_cat = next((c for c in cats if c != primary_cat), cats[0])
        suggestions.append(f"Average {secondary_metric} grouped by {other_cat}")
    else:
        suggestions.append(f"Summary statistics and outliers for {primary_metric}")

    if dtype_mode == "stock_summary":
        suggestions.append("What should I reorder this week?")

    # Always include a dynamic CSV generation prompt!
    if primary_cat:
        suggestions.append(f"Generate a CSV summarizing {primary_metric} by {primary_cat}")
    else:
        suggestions.append(f"Generate a CSV of top 25 rows sorted by {primary_metric}")

    suggestions.append("Explain all rows and columns in this dataset")
    return suggestions[:6]


def suggest_column_mapping_heuristics(
    df: pl.DataFrame,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, str], Dict[str, float], Dict[str, str]]:
    """
    Universally maps raw dataframe columns to logical schema fields and builds rich metadata.
    Supports:
    1. Multi-series or single-series Time Series (daily, weekly, monthly, quarterly, yearly)
    2. ERP Stock / Inventory Summary reports
    3. Any General Tabular / Business Dataset (customers, finance, HR, housing, operations, etc.)
    """
    suggested_mapping: Dict[str, Any] = {}
    confidence: Dict[str, float] = {}
    reasons: Dict[str, str] = {}

    if len(df.columns) == 0:
        return suggested_mapping, confidence, reasons

    profiles = build_column_profiles(df)
    norm_col_map = {col: _normalize_name(col) for col in df.columns}
    used_columns = set()

    # 1. Detect genuine date/temporal columns
    date_candidates: List[Tuple[str, float, str]] = []
    for col in df.columns:
        norm = norm_col_map[col]
        prof = profiles.get(col, {})
        is_temp = df[col].dtype in (pl.Date, pl.Datetime)
        rate = 1.0 if is_temp else (check_date_parse_rate(df, col) if prof.get("role") == "temporal" or df[col].dtype not in NUMERIC_DTYPES else 0.0)
        has_date_kw = any(kw == norm or kw in norm for kw in LOGICAL_FIELDS["date"]["keywords"])
        has_avoid = any(av in norm for av in LOGICAL_FIELDS["date"]["avoid"]) and not has_date_kw

        if has_avoid and rate < 0.8:
            continue
        if is_temp:
            date_candidates.append((col, 0.99, "Native Date/Datetime column"))
        elif rate >= 0.75:
            bonus = 0.15 if has_date_kw else 0.0
            date_candidates.append((col, min(0.98, round(0.80 + bonus, 2)), f"Parsed {int(rate * 100)}% of values as calendar dates"))
        elif rate >= 0.45 and has_date_kw:
            date_candidates.append((col, 0.75, f"Temporal header & {int(rate * 100)}% valid dates"))

    date_candidates.sort(key=lambda x: x[1], reverse=True)

    # 2. Check for Stock Summary columns (Closing stock + Sales quantity without date column)
    stock_col = None
    for col, norm in norm_col_map.items():
        if df[col].dtype in NUMERIC_DTYPES and any(kw == norm or kw in norm for kw in LOGICAL_FIELDS["stock_on_hand"]["keywords"]):
            if not any(av in norm for av in LOGICAL_FIELDS["stock_on_hand"]["avoid"]):
                stock_col = col
                break

    sale_col = None
    for col, norm in norm_col_map.items():
        if df[col].dtype in NUMERIC_DTYPES and any(kw == norm or kw in norm for kw in ("net_sale_qty", "sale_qty", "sales_qty", "units_sold", "sold_qty")):
            if not any(av in norm for av in ("inward", "outward", "closing", "opening", "value", "rate", "price", "profit")):
                sale_col = col
                break

    is_stock_summary = (not date_candidates) and (
        (stock_col is not None and sale_col is not None)
        or (stock_col is not None and meta and "stock" in str(meta).lower())
    )

    if is_stock_summary:
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

        for col, norm in norm_col_map.items():
            if col not in used_columns and norm in ("brand", "product", "item", "sku", "department", "article"):
                suggested_mapping["product"] = col
                confidence["product"] = 0.90
                reasons["product"] = f"Categorical merchandise column matched '{col}'"
                used_columns.add(col)
                break

        for col, norm in norm_col_map.items():
            if col not in used_columns and any(kw in norm for kw in ("gross_item_sale", "gross_sales", "sale_value", "revenue")):
                suggested_mapping["revenue"] = col
                confidence["revenue"] = 0.85
                reasons["revenue"] = f"Sales value matched column '{col}'"
                used_columns.add(col)
                break

        for col, norm in norm_col_map.items():
            if col not in used_columns and any(kw in norm for kw in ("vendor", "supplier", "party")):
                suggested_mapping["vendor"] = col
                confidence["vendor"] = 0.85
                reasons["vendor"] = f"Supplier vendor matched column '{col}'"
                used_columns.add(col)
                break

    elif date_candidates:
        # Time Series Mode (works for retail sales, financial/macro/housing series, single-series, etc.)
        suggested_mapping["_dataset_type"] = "time_series"
        best_date_col, best_date_conf, best_date_reason = date_candidates[0]
        suggested_mapping["date"] = best_date_col
        confidence["date"] = best_date_conf
        reasons["date"] = best_date_reason
        used_columns.add(best_date_col)

        # Score remaining columns for optional & required fields
        scores: Dict[str, List[Tuple[str, float, str]]] = {field: [] for field in LOGICAL_FIELDS if field != "date"}

        for col in df.columns:
            if col in used_columns:
                continue
            norm = norm_col_map[col]
            prof = profiles.get(col, {})
            role = prof.get("role", "")
            is_numeric = df[col].dtype in NUMERIC_DTYPES

            for field, cfg in LOGICAL_FIELDS.items():
                if field == "date":
                    continue
                if any(av in norm for av in cfg.get("avoid", [])):
                    continue

                score = 0.0
                reason_parts = []
                norm_tokens = norm.split("_")

                if norm in cfg["keywords"]:
                    score += 0.72
                    reason_parts.append("Exact column name match")
                elif any(kw in norm_tokens or (len(kw) >= 4 and kw in norm) for kw in cfg["keywords"]):
                    score += 0.42
                    reason_parts.append("Column name match")

                if field == "quantity":
                    if not is_numeric:
                        continue
                    score += 0.25
                    reason_parts.append("Numeric metric")
                    # Prefer continuous measures over tiny discrete attribute counts (e.g. MA over bedrooms)
                    if role == "numeric_measure":
                        score += 0.18
                    elif role == "numeric_attribute":
                        score -= 0.20
                elif field == "product":
                    if is_numeric or role == "temporal":
                        continue
                    score += 0.25
                    reason_parts.append("Categorical/entity column")
                    # Prefer moderate-cardinality grouping columns over free text
                    u_cnt = prof.get("unique_count", 0)
                    if 1 <= u_cnt <= max(500, len(df) // 2 + 1):
                        score += 0.12
                elif field in ("revenue", "stock_on_hand", "profit", "cost", "discount"):
                    if not is_numeric:
                        continue
                    score += 0.25
                    reason_parts.append("Numeric type")

                if score >= 0.30:
                    scores[field].append((col, min(round(score, 2), 1.0), "; ".join(reason_parts)))

        # Map revenue first ONLY if there is a separate stronger quantity column; otherwise map quantity first
        priority_order = ["quantity", "product", "revenue", "stock_on_hand", "vendor", "store", "promo_flag"]
        for field in priority_order:
            candidates = sorted(scores.get(field, []), key=lambda x: x[1], reverse=True)
            for col, sc, rsn in candidates:
                if col not in used_columns:
                    suggested_mapping[field] = col
                    confidence[field] = sc
                    reasons[field] = rsn
                    used_columns.add(col)
                    break

        # Fallback for quantity: pick highest-variance numeric_measure column (never date!)
        if "quantity" not in suggested_mapping:
            num_cands = [
                c for c in df.columns
                if c not in used_columns and df[c].dtype in NUMERIC_DTYPES and profiles.get(c, {}).get("role") == "numeric_measure"
            ]
            if not num_cands:
                num_cands = [c for c in df.columns if c not in used_columns and df[c].dtype in NUMERIC_DTYPES]
            if num_cands:
                best_num = max(num_cands, key=lambda c: profiles.get(c, {}).get("unique_count", 0))
                suggested_mapping["quantity"] = best_num
                confidence["quantity"] = 0.75
                reasons["quantity"] = f"Primary numeric measure '{best_num}'"
                used_columns.add(best_num)

        # Fallback for product: pick categorical/attribute column, OR synthesize '__overall__' for single-series time series!
        if "product" not in suggested_mapping:
            cat_cands = [
                c for c in df.columns
                if c not in used_columns and profiles.get(c, {}).get("role") in ("categorical", "identifier", "text")
            ]
            if not cat_cands:
                cat_cands = [
                    c for c in df.columns
                    if c not in used_columns and profiles.get(c, {}).get("role") == "numeric_attribute"
                ]
            if cat_cands:
                suggested_mapping["product"] = cat_cands[0]
                confidence["product"] = 0.75
                reasons["product"] = f"Grouping entity column '{cat_cands[0]}'"
                used_columns.add(cat_cands[0])
            else:
                suggested_mapping["product"] = "__overall__"
                confidence["product"] = 0.90
                reasons["product"] = "Single-series temporal dataset (overall series)"

        # Check if secondary low-cardinality dimensions exist that refine the time series grain
        # e.g. in ma_lga_12345.csv: saledate + type + bedrooms
        prod_c = suggested_mapping.get("product")
        if prod_c and prod_c in df.columns:
            pair_unique = int(df.select([best_date_col, prod_c]).n_unique())
            if pair_unique < int(len(df) * 0.85):
                extra_dims = [
                    c for c in df.columns
                    if c not in (best_date_col, prod_c, suggested_mapping.get("quantity"), suggested_mapping.get("revenue"), suggested_mapping.get("store"), suggested_mapping.get("promo_flag"))
                    and profiles.get(c, {}).get("role") in ("categorical", "numeric_attribute")
                    and 2 <= profiles.get(c, {}).get("unique_count", 0) <= 12
                ]
                if extra_dims:
                    test_u = int(df.select([best_date_col, prod_c, extra_dims[0]]).n_unique())
                    if test_u > pair_unique * 1.5 and test_u >= int(len(df) * 0.90):
                        suggested_mapping["_composite_product_cols"] = [prod_c, extra_dims[0]]

    else:
        # 3. Universal General Tabular Mode (no date column needed — works for ANY CSV/Excel/JSON!)
        suggested_mapping["_dataset_type"] = "tabular"
        suggested_mapping["date"] = "__tabular__"
        confidence["date"] = 0.95
        reasons["date"] = "Cross-sectional tabular dataset (no date column required)"

        # Pick best entity / category column
        cat_cols = [c for c in df.columns if profiles.get(c, {}).get("role") in ("categorical", "identifier", "text")]
        if cat_cols:
            # Prefer a categorical grouping column with >=2 values, or identifier
            grp_cats = [c for c in cat_cols if profiles.get(c, {}).get("role") == "categorical" and profiles.get(c, {}).get("unique_count", 0) >= 2]
            best_cat = grp_cats[0] if grp_cats else cat_cols[0]
            suggested_mapping["product"] = best_cat
            confidence["product"] = 0.85
            reasons["product"] = f"Primary entity/category column '{best_cat}'"
            used_columns.add(best_cat)
        else:
            suggested_mapping["product"] = df.columns[0]
            confidence["product"] = 0.70
            reasons["product"] = f"Primary column '{df.columns[0]}'"
            used_columns.add(df.columns[0])

        # Pick best numeric measure column (or '__count__' if table has only text/categorical columns)
        num_cols = [c for c in df.columns if c not in used_columns and df[c].dtype in NUMERIC_DTYPES]
        if num_cols:
            measures = [c for c in num_cols if profiles.get(c, {}).get("role") == "numeric_measure"]
            best_num = measures[0] if measures else num_cols[0]
            suggested_mapping["quantity"] = best_num
            confidence["quantity"] = 0.85
            reasons["quantity"] = f"Primary numeric metric '{best_num}'"
            used_columns.add(best_num)
        else:
            suggested_mapping["quantity"] = "__count__"
            confidence["quantity"] = 0.80
            reasons["quantity"] = "Record count metric (categorical table)"

    # Attach rich column profiles, semantics, row grain, and starter prompts
    row_grain = detect_row_grain(
        df,
        profiles,
        date_col=suggested_mapping.get("date") if suggested_mapping.get("date") in df.columns else None,
    )
    suggested_mapping["_column_semantics"] = explain_columns(df.columns, df=df)
    suggested_mapping["_column_profiles"] = profiles
    suggested_mapping["_row_grain"] = row_grain
    suggested_mapping["_suggested_questions"] = generate_suggested_questions(df, profiles, suggested_mapping)
    if meta and meta.get("date_range"):
        suggested_mapping["_date_range"] = meta.get("date_range")
    if meta and meta.get("report_name"):
        suggested_mapping["_report_name"] = meta.get("report_name")

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


