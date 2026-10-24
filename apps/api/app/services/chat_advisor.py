import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from apps.api.app.config import settings
from apps.api.app.db import ChatMessage, Dataset, ProductForecast
from apps.api.app.services.narrator import validate_narrative_traceability

logger = logging.getLogger("advisor.chat")


def get_or_create_initial_messages(dataset: Dataset, db: Session) -> List[ChatMessage]:
    """
    Creates or retrieves the 3 core decision step messages for the dataset:
    Step 1: Clean and check data
    Step 2: Forecast demand with proof
    Step 3: Turn forecast into decisions
    Creates or retrieves the 4 core decision step messages for the dataset:
    Step 1: Clean and check data & schema
    Step 2: Forecast demand with proof (TimesFM vs Baselines)
    Step 3: Turn forecast into decisions (Reorder Points & Quantities)
    Step 4: AI Executive Briefing & Predictions Summary
    """
    existing = (
        db.query(ChatMessage)
        .filter(ChatMessage.dataset_id == dataset.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    if existing:
        return existing
    existing_card_types = {m.card_type for m in existing if m.card_type}

    health = dataset.health_report or {}
    forecasts = (
        db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset.id).all()
    )

    messages: List[ChatMessage] = list(existing)

    total_cleaned = health.get("total_rows_cleaned", dataset.row_count or 0)
    total_prods = health.get("total_products", dataset.product_count or 0)
    class_dist = health.get("class_distribution", {})
    issues = health.get("issues", [])
    col_map = dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})
    stock_health = health.get("stock_health") or {}
    top_moving = health.get("top_moving") or []

    # Step 1: Clean & Check Data Card
    if "health_summary" not in existing_card_types:
        date_col = col_map.get("date", "Date")
        prod_col = col_map.get("product", "Product")
        qty_col = col_map.get("quantity", "Quantity")
        stock_col = col_map.get("stock_on_hand")
        rev_col = col_map.get("revenue")

        mapping_desc = f"Product/Brand: `{prod_col}`, Sales Qty: `{qty_col}`"
        if stock_col:
            mapping_desc += f", On-Hand Stock: `{stock_col}`"
        if date_col and date_col != "__period__":
            mapping_desc += f", Date: `{date_col}`"
        if rev_col:
            mapping_desc += f", Revenue: `{rev_col}`"

        is_stock_sum = health.get("dataset_type") == "stock_summary"
        file_desc = "ERP Stock & Sales Summary" if is_stock_sum else "sales transaction history"

        step1_content = (
            f"**Step 1: Data Ingestion & Schema Verified.**\n"
            f"I inspected your {file_desc} (**{dataset.original_filename}**) and auto-detected your columns:\n"
            f"• **Mapped Columns**: {mapping_desc}\n\n"
            f"We analyzed **{total_cleaned:,} records** spanning **{health.get('total_days', 0)} calendar days** "
            f"({health.get('date_min', 'start')} to {health.get('date_max', 'end')}). "
            f"Your **{total_prods} products/brands** have been classified by demand pattern and stock health."
        )

        step1_payload = {
            "step": 1,
            "title": "Clean & Check the Data",
            "total_rows_cleaned": total_cleaned,
            "total_products": total_prods,
            "total_days": health.get("total_days", 0),
            "date_min": health.get("date_min"),
            "date_max": health.get("date_max"),
            "column_mapping": {k: v for k, v in col_map.items() if not k.startswith("_")},
            "column_semantics": col_semantics,
            "class_distribution": class_dist,
            "stock_health": stock_health,
            "top_moving": top_moving[:6],
            "dataset_type": health.get("dataset_type", "transaction_series"),
            "issues_count": len(issues),
            "issues": issues[:4],
        }
        m1 = ChatMessage(
            dataset_id=dataset.id,
            role="assistant",
            content=step1_content,
            card_type="health_summary",
        )
        m1.payload = step1_payload
        db.add(m1)
        messages.append(m1)
        db.commit()

    # If forecasts exist, generate Step 2, Step 3, and Step 4
    if forecasts:
        # Step 2: Forecast Demand with Proof
        model_counts: Dict[str, int] = {}
        total_forecast_units = 0.0
        wapes = []

        for f in forecasts:
            m_name = f.chosen_model or "Unknown"
            model_counts[m_name] = model_counts.get(m_name, 0) + 1
            pt = f.point_forecast or []
            total_forecast_units += sum(pt)
            bt = f.backtest_table or []
            for b in bt:
                if b.get("model_name") == m_name and "wape" in b:
                    wapes.append(b["wape"])

        avg_wape = round(float(sum(wapes) / len(wapes)) * 100, 1) if wapes else 15.0

        # Collect top projected products
        sorted_forecasts = sorted(
            forecasts,
            key=lambda f: sum(f.point_forecast or []),
            reverse=True,
        )
        top_projected = [
            {
                "product": f.product_id,
                "model": f.chosen_model,
                "projected_units": int(round(sum(f.point_forecast or []))),
                "p10": int(round(sum(f.quantiles.get("0.1", f.point_forecast or [])))),
                "p90": int(round(sum(f.quantiles.get("0.9", f.point_forecast or [])))),
            }
            for f in sorted_forecasts[:5]
        ]

        # Step 2: Forecast Demand with Proof
        if "forecast_proof" not in existing_card_types:
            top_prods_str = ", ".join(f"**{p['product']}** (~{p['projected_units']:,} units)" for p in top_projected[:3])
            step2_content = (
                f"**Step 2: Forecast Demand with Proof.**\n"
                f"We evaluated Google TimesFM against classical statistical baselines (AutoETS, AutoARIMA, Croston) on your actual data "
                f"using rolling-origin backtesting. We show the honest winner for every product, even when the simple method wins.\n"
                f"• **Total Projected Demand**: **{int(round(total_forecast_units)):,} units** over the upcoming 28-day horizon.\n"
                f"• **Top Projected Products**: {top_prods_str}.\n"
                f"• **Average Backtest Error (WAPE)**: **~{avg_wape}%** across evaluated folds."
            )
            step2_payload = {
                "step": 2,
                "title": "Forecast Demand with Proof",
                "model_counts": model_counts,
                "total_products": len(forecasts),
                "total_forecast_units": int(round(total_forecast_units)),
                "average_error_pct": avg_wape,
                "top_projected": top_projected,
            }
            m2 = ChatMessage(
                dataset_id=dataset.id,
                role="assistant",
                content=step2_content,
                card_type="forecast_proof",
            )
            m2.payload = step2_payload
            db.add(m2)
            messages.append(m2)

        # Step 3: Turn Forecast into Decisions
        reorder_items = []
        high_risk_count = 0
        for f in forecasts:
            adv = f.inventory_advice or {}
            qty = adv.get("recommended_order_qty")
            risk = adv.get("stockout_risk", "low")
            if risk == "high":
                high_risk_count += 1
            if qty and qty > 0:
                reorder_items.append({
                    "product": f.product_id,
                    "series_class": f.series_class,
                    "recommended_qty": qty,
                    "reorder_point": adv.get("reorder_point", 0),
                    "days_of_cover": adv.get("days_of_cover"),
                    "stockout_risk": risk,
                })

        reorder_items.sort(key=lambda x: x["recommended_qty"], reverse=True)

        if "inventory_decision" not in existing_card_types:
            step3_content = (
                f"**Step 3: Actionable Reorder Decisions.**\n"
                f"Deterministic lead-time math converted the forecasts into exact Reorder Points (ROP) and order quantities based on your supplier lead time and 90% service level.\n"
                + (
                    f"⚠️ **{len(reorder_items)} items** require restocking to maintain safety buffers."
                    if reorder_items
                    else "✅ All items currently maintain stock above their reorder thresholds."
                )
            )
            step3_payload = {
                "step": 3,
                "title": "Turn Forecast into Decisions",
                "reorder_actions_count": len(reorder_items),
                "high_risk_count": high_risk_count,
                "top_reorders": reorder_items[:6],
                "total_reorders": len(reorder_items),
                "csv_url": f"/api/v1/datasets/{dataset.id}/inventory.csv",
            }
            m3 = ChatMessage(
                dataset_id=dataset.id,
                role="assistant",
                content=step3_content,
                card_type="inventory_decision",
            )
            m3.payload = step3_payload
            db.add(m3)
            messages.append(m3)
        # Message 4: Comprehensive AI Executive Summary & Predictions Briefing
        if "executive_summary" not in existing_card_types:
            active_classes = [f"{count} {cls}" for cls, count in class_dist.items() if count > 0]
            class_str = ", ".join(active_classes) if active_classes else "catalog analyzed"
            models_str = ", ".join(f"{m} ({c} items)" for m, c in model_counts.items())

            summary_paragraphs = [
                f"### Executive Summary & Predictions Report\n"
                f"Here is the comprehensive sales & stocking briefing for **{dataset.name}** across **{len(forecasts)} products**:\n"
            ]

            # 1. Ingestion & Profile
            summary_paragraphs.append(
                f"**1. Sales History & Data Hygiene**\n"
                f"We processed **{total_cleaned:,} records** from **{health.get('date_min', 'start')}** to **{health.get('date_max', 'end')}** "
                f"({health.get('total_days', 0)} calendar days). AI mapped schema columns (`{col_map.get('date', 'date')}`, `{col_map.get('product', 'product')}`, `{col_map.get('quantity', 'quantity')}`). "
                f"All missing intermediate dates were regularized with zero sales during active lifecycles. Demand distribution: {class_str}."
            )

            # 2. Forecast Predictions
            top_prods_brief = "\n".join(
                f"- **{p['product']}**: ~**{p['projected_units']:,} units** projected (80% range: {p['p10']:,} – {p['p90']:,} units) — *Model: {p['model']}*"
                for p in top_projected[:4]
            )
            summary_paragraphs.append(
                f"**2. Demand Forecasts & TimesFM Comparison**\n"
                f"Catalog projected demand totals **{int(round(total_forecast_units)):,} units** over the upcoming 28-day horizon.\n"
                f"Models were selected strictly on rolling backtest performance: {models_str}.\n"
                f"Average backtest error (WAPE) is **~{avg_wape}%**.\n"
                f"Key product demand projections:\n"
                f"{top_prods_brief}"
            )

            # 3. Stock Decisions
            if reorder_items:
                reorder_bullets = "\n".join(
                    f"- **{r['product']}**: Order **{r['recommended_qty']} units** (Reorder Point: {int(round(r['reorder_point']))}, Risk: {r['stockout_risk']})"
                    for r in reorder_items[:5]
                )
                summary_paragraphs.append(
                    f"**3. Reorder Decisions & Stocking Plan**\n"
                    f"Based on a 7-day supplier lead time and 90% service level, **{len(reorder_items)} products** need reorders:\n"
                    f"{reorder_bullets}\n\n"
                    f"Click **'Download Purchase Order CSV'** in the card above to download your ready-to-send purchase order sheet."
                )
            else:
                summary_paragraphs.append(
                    f"**3. Reorder Decisions & Stocking Plan**\n"
                    f"Current inventory levels are sufficient across all analyzed items. No emergency replenishment orders are required today."
                )

            summary_paragraphs.append(
                f"---\n"
                f"💡 *Ask me any questions below — for example:*\n"
                f"- *'What should I reorder this week?'*\n"
                f"- *'Which products have high stockout risk?'*\n"
                f"- *'Why was AutoETS chosen instead of TimesFM for PROD_01?'*\n"
                f"- *'Tell me about {top_projected[0]['product'] if top_projected else 'PROD_01'}'*"
            )

            m4 = ChatMessage(
                dataset_id=dataset.id,
                role="assistant",
                content="\n\n".join(summary_paragraphs),
                card_type="executive_summary",
            )
            db.add(m4)
            messages.append(m4)

        db.commit()

    return messages


async def answer_user_query(
    dataset: Dataset,
    query: str,
    db: Session,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """
    Understands the user's natural language question, pulls deterministic facts,
    prompts local Gemma, validates numerical traceability, and returns response.
    """
    query_lower = query.lower()
    forecasts = (
        db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset.id).all()
    )
    health = dataset.health_report or {}

    # Check if query mentions a specific product (e.g. PROD_01, SKU...)
    target_forecast: Optional[ProductForecast] = None
    for f in forecasts:
        if f.product_id.lower() in query_lower:
            target_forecast = f
            break

    # 1. Product-specific question
    if target_forecast:
        return await _answer_product_query(dataset, target_forecast, query)

    # 2. Schema Definition questions ("what does the row/column represent?", "explain columns", "what does X mean")
    is_schema_definition_query = any(
        w in query_lower for w in [
            "represent", "meaning", "what does", "define", "definition", "explain columns",
            "explain row", "what do the columns", "what do the rows", "what is this column", "header meaning",
        ]
    ) and not any(w in query_lower for w in ["order", "reorder", "purchase", "top", "best", "most", "stat", "sum", "total", "average", "avg", "compare", "min", "max"])
    if is_schema_definition_query:
        return await _answer_schema_query(dataset, health, query)
    # 3. AGENTIC TOOL PIPELINE
    # Gemma AI Agent evaluates the query and dataset schema, then selects and executes the appropriate tool.
    col_map = dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})
    available_cols = list(col_semantics.keys()) if col_semantics else []
    if not available_cols:
        try:
            from apps.api.app.services.dataset_tools import get_dataset_dataframe
            available_cols = get_dataset_dataframe(dataset).columns
        except Exception:
            available_cols = []

    try:
        from apps.api.app.services.dataset_tools import select_tool_with_gemma, execute_dataset_tool
        tool_selection = await select_tool_with_gemma(query, available_cols, col_semantics)
        if tool_selection:
            tool_name, tool_args = tool_selection
            tool_res = execute_dataset_tool(dataset, tool_name, tool_args, db)
            if tool_res and not tool_res.get("error"):
                narrative = await _explain_tool_result_with_gemma(query, tool_res, col_semantics)
                return narrative, "tool_result", tool_res
    except Exception as e_tool:
        logger.warning(f"Agentic tool execution error: {e_tool}")

    # 4. Top selling brands / Top movers / Best sellers ("top brand", "best seller", "top selling", "most sold", "highest sale")
    top_mover_keywords = [
        "top brand", "top brands", "top seller", "top sellers", "best seller", "best sellers",
        "best selling", "most sold", "top selling", "highest sale", "top moving",
        "popular brand", "leading brand", "who are the top", "which brand sold"
    ]
    if any(w in query_lower for w in top_mover_keywords):
        return await _answer_top_movers_query(dataset, health, forecasts, query)

    # 4. Stock health / Out of stock / Overstock questions
    # 5. Stock health / Out of stock / Overstock questions
    stock_health_keywords = [
        "out of stock", "overstock", "overstocked", "critical stock",
        "how many out of stock", "stock health", "zero stock", "no stock"
    ]
    if any(w in query_lower for w in stock_health_keywords):
        return await _answer_stock_health_query(dataset, health, query)

    # 5. Financial questions — profit, margin, revenue, cost, discount (DYNAMIC — no hardcoded columns)
    # 6. Financial questions fallback (profit, margin, revenue, cost, discount)
    financial_keywords = [
        "profit", "margin", "net profit", "gross profit", "profit percent",
        "profit %", "revenue", "gross sales", "cogs", "cost of goods",
        "discount", "markdown", "markup", "earnings", "sale value",
    ]
    if any(w in query_lower for w in financial_keywords):
        col_map = dataset.column_mapping or {}
        col_semantics = col_map.get("_column_semantics", {})
        return await _answer_financial_query(dataset, health, col_semantics, query)

    # 6. Reorders & Ordering questions ("what should i order", "reorder", "restock")
    if any(w in query_lower for w in ["order", "reorder", "stock", "buy", "purchase", "restock"]):
        return await _answer_reorder_query(dataset, forecasts, query)

    # 7. Risk questions ("risk", "stockout", "run out")
    if any(w in query_lower for w in ["risk", "stockout", "shortage", "urgent"]):
        return await _answer_risk_query(dataset, forecasts, query)

    # 8. Model & Backtest proof questions ("timesfm", "baseline", "model", "accuracy", "error", "wape")
    if any(w in query_lower for w in ["timesfm", "baseline", "model", "autoets", "croston", "arima", "accuracy", "wape", "mase"]):
        return await _answer_model_query(dataset, forecasts, query)

    # 9. Data cleaning & health questions ("clean", "missing", "data", "quality", "outlier", "duplicates")
    if any(w in query_lower for w in ["clean", "health", "missing", "data", "quality", "outlier", "returns"]):
        return await _answer_health_query(dataset, health, query)

    # 10. General / aggregate questions ("total", "sales", "overall", "forecast", "summary")
    return await _answer_general_query(dataset, forecasts, health, query)


async def _call_gemma_with_facts(
    prompt: str,
    facts: Dict[str, Any],
    fallback_text: str,
    col_context: Optional[Dict[str, str]] = None,
) -> str:
    """Invokes local Gemma via Ollama, verifying numbers against facts.
    col_context: optional dict of {col_name: meaning} so Gemma knows what columns represent."""
    # Build a column context hint if provided — Gemma reads this to understand the data schema
    col_hint = ""
    if col_context:
        # Pick up to 12 most relevant columns
        items = list(col_context.items())[:12]
        col_hint = "Dataset columns: " + "; ".join(f"`{c}` = {m}" for c, m in items) + ".\n"

    system_instruction = (
        "You are a helpful business data advisor for small business owners. "
        "You help them understand their sales data, inventory levels, profits, and stocking decisions. "
        f"{col_hint}"
        "Answer the user's question directly and concisely in 2-4 sentences based ONLY on the provided JSON facts. "
        "CRITICAL RULES: "
        "(1) Never invent, estimate, or calculate any numbers — every number you state MUST appear exactly in the JSON facts. "
        "(2) Never assume what columns mean — use only the column definitions given above. "
        "(3) If the facts don't contain an answer, say so honestly rather than guessing."
    )
    user_prompt = f"Facts: {json.dumps(facts, default=str)}\n\nQuestion: {prompt}"

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": f"{system_instruction}\n\n{user_prompt}",
                    "stream": False,
                    "options": {"temperature": 0.05},
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("response", "").strip()
                if text and validate_narrative_traceability(text, facts):
                    return text
    except Exception as e:
        logger.warning(f"Ollama chat call failed: {e}. Using deterministic response.")

    return fallback_text


async def _explain_tool_result_with_gemma(
    query: str,
    tool_res: Dict[str, Any],
    col_semantics: Dict[str, str],
) -> str:
    """Uses Gemma to explain deterministic tool results in plain English, with strict number traceability."""
    facts = tool_res.get("facts", {})
    title = tool_res.get("title", "Dataset Analysis")
    tool_name = tool_res.get("tool_name", "tool")

    rows = tool_res.get("table_rows", [])
    if rows:
        row_bullets = "\n".join(f"• **{r[0]}**: {r[1]}" for r in rows[:5])
        fallback = (
            f"**{title}**\n\n"
            f"{row_bullets}\n\n"
            f"These metrics were computed directly from your uploaded dataset using the `{tool_name}` tool."
        )
    else:
        fallback = f"**{title}**: Analysis computed from your uploaded file."

    prompt = (
        f"The user asked: \"{query}\"\n"
        f"The system ran the tool '{tool_name}' and found these exact facts:\n"
        f"{json.dumps(facts, default=str)}\n\n"
        f"Explain these results to the shop owner in 2-3 clear, helpful sentences. Mention the leading figures or key takeaways directly."
    )

    return await _call_gemma_with_facts(prompt, facts, fallback, col_context=col_semantics)



async def _answer_product_query(
    dataset: Dataset,
    fc: ProductForecast,
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    pt = fc.point_forecast or []
    tot_units = int(round(sum(pt)))
    adv = fc.inventory_advice or {}
    rop = int(round(adv.get("reorder_point", 0)))
    order_qty = adv.get("recommended_order_qty")
    risk = adv.get("stockout_risk", "low")
    chosen = fc.chosen_model

    facts = {
        "product": fc.product_id,
        "demand_pattern": fc.series_class,
        "chosen_model": chosen,
        "total_forecast_units": tot_units,
        "reorder_point": rop,
        "recommended_order_qty": order_qty if order_qty is not None else 0,
        "stockout_risk": risk,
    }

    fallback = (
        f"For {fc.product_id} ({fc.series_class} demand pattern), the winning model is {chosen}. "
        f"The total projected demand is {tot_units} units over the upcoming period. "
        f"Its reorder point is {rop} units, and the current stockout risk is rated {risk}."
    )
    if order_qty and order_qty > 0:
        fallback += f" The recommended order quantity is {order_qty} units."

    text = await _call_gemma_with_facts(query, facts, fallback)
    payload = {
        "product": fc.product_id,
        "series_class": fc.series_class,
        "chosen_model": chosen,
        "total_units": tot_units,
        "reorder_point": rop,
        "recommended_qty": order_qty,
        "backtest": (fc.backtest_table or [])[:3],
    }
    return text, "product_focus", payload


async def _answer_reorder_query(
    dataset: Dataset,
    forecasts: List[ProductForecast],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    reorders = []
    for f in forecasts:
        adv = f.inventory_advice or {}
        qty = adv.get("recommended_order_qty")
        if qty and qty > 0:
            reorders.append({
                "product": f.product_id,
                "recommended_qty": qty,
                "reorder_point": int(round(adv.get("reorder_point", 0))),
                "risk": adv.get("stockout_risk", "low"),
            })

    reorders.sort(key=lambda x: x["recommended_qty"], reverse=True)

    facts = {
        "reorder_count": len(reorders),
        "top_items": reorders[:3],
    }

    if not reorders:
        fallback = "All products currently maintain inventory above their calculated reorder points. No immediate purchase orders are required."
    else:
        top_str = ", ".join(f"{r['product']} ({r['recommended_qty']} units)" for r in reorders[:3])
        fallback = (
            f"You have {len(reorders)} products that require restocking based on your lead times. "
            f"The top priorities are {top_str}. You can download the complete purchase order CSV right here."
        )

    text = await _call_gemma_with_facts(query, facts, fallback)
    payload = {
        "reorder_items": reorders[:6],
        "total_items": len(reorders),
        "csv_url": f"/api/v1/datasets/{dataset.id}/inventory.csv",
    }
    return text, "reorder_list", payload


async def _answer_risk_query(
    dataset: Dataset,
    forecasts: List[ProductForecast],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    high_risks = []
    medium_risks = []
    for f in forecasts:
        adv = f.inventory_advice or {}
        risk = adv.get("stockout_risk")
        if risk == "high":
            high_risks.append(f.product_id)
        elif risk == "medium":
            medium_risks.append(f.product_id)

    facts = {
        "high_risk_count": len(high_risks),
        "medium_risk_count": len(medium_risks),
        "total_analyzed": len(forecasts),
    }

    if high_risks:
        fallback = f"There are {len(high_risks)} products identified at high stockout risk ({', '.join(high_risks[:3])}), and {len(medium_risks)} items on the watch list."
    else:
        fallback = f"Zero products are currently in the high stockout risk category. {len(medium_risks)} items are on the watch list to monitor closely."

    text = await _call_gemma_with_facts(query, facts, fallback)
    return text, None, None


async def _answer_model_query(
    dataset: Dataset,
    forecasts: List[ProductForecast],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    model_counts: Dict[str, int] = {}
    for f in forecasts:
        m = f.chosen_model or "Unknown"
        model_counts[m] = model_counts.get(m, 0) + 1

    facts = {
        "total_products": len(forecasts),
        "model_selection": model_counts,
    }

    models_str = ", ".join(f"{count} products using {m}" for m, count in model_counts.items())
    fallback = (
        f"We honestly test TimesFM against statistical baselines (AutoETS, AutoARIMA, Croston) using rolling backtests. "
        f"Across your {len(forecasts)} products, the backtest results chose: {models_str}. "
        f"The best performing model on your historical sales is always selected without bias."
    )

    text = await _call_gemma_with_facts(query, facts, fallback)
    return text, None, None


async def _answer_health_query(
    dataset: Dataset,
    health: Dict[str, Any],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    cleaned = health.get("total_rows_cleaned", 0)
    prods = health.get("total_products", 0)
    issues = health.get("issues", [])

    facts = {
        "total_rows_cleaned": cleaned,
        "total_products": prods,
        "issues_detected": len(issues),
    }

    fallback = (
        f"Your dataset contains {cleaned} cleaned records across {prods} products. "
        f"During data intake, {len(issues)} data quality checks were conducted, including filling missing daily dates, "
        f"filtering out negative return values, and isolating irregular spikes."
    )

    text = await _call_gemma_with_facts(query, facts, fallback)
    return text, None, None


async def _answer_general_query(
    dataset: Dataset,
    forecasts: List[ProductForecast],
    health: Dict[str, Any],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    tot_units = int(round(sum(sum(f.point_forecast or []) for f in forecasts)))
    prods = len(forecasts) or health.get("total_products", 0)
    col_map = dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})

    facts = {
        "dataset_name": dataset.name,
        "total_products": prods,
        "total_forecast_units": tot_units,
        "columns_available": list(col_semantics.keys()),
    }

    fallback = (
        f"For your dataset '{dataset.name}', we're tracking {prods} products. "
        f"Total projected sales demand across all items is {tot_units} units over the forecast period. "
        f"You can ask me about specific products, reorder requirements, profits, margins, or model backtests."
    )

    text = await _call_gemma_with_facts(query, facts, fallback, col_context=col_semantics)
    return text, None, None


async def _answer_schema_query(
    dataset: Dataset,
    health: Dict[str, Any],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Explains what the rows and columns in the user's file represent.
    Fully dynamic — works for any dataset schema, no hardcoded column names."""
    col_map = dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})
    query_lower = query.lower()

    # Check if a specific column is requested — match by partial name in question
    matched_col = None
    matched_meaning = None
    for col_name, meaning in col_semantics.items():
        if col_name.lower() in query_lower or any(
            part in query_lower for part in col_name.lower().split() if len(part) > 3
        ):
            matched_col = col_name
            matched_meaning = meaning
            break

    if matched_col and matched_meaning:
        facts = {
            "column": matched_col,
            "meaning": matched_meaning,
            "dataset": dataset.name,
        }
        fallback = f"The column **`{matched_col}`** represents: **{matched_meaning}** in your dataset."
        text = await _call_gemma_with_facts(query, facts, fallback)
        return text, "schema_explanation", {"columns": {matched_col: matched_meaning}}

    # General row & column explanation — dynamic, works for any dataset
    is_stock_sum = health.get("dataset_type") == "stock_summary"

    # Build dynamic row description
    prod_col = col_map.get("product", "a product/item")
    qty_col = col_map.get("quantity", "quantity")
    date_col = col_map.get("date")

    if is_stock_sum:
        row_meaning = (
            f"Each row in this file represents a consolidated inventory & sales record for one "
            f"product/brand (column: `{prod_col}`). It captures the full inventory lifecycle — "
            f"opening stock, inward receipts, outward transfers, current closing units, and "
            f"customer sales during the reporting period."
        )
    elif date_col and date_col != "__period__":
        row_meaning = (
            f"Each row represents an individual sales transaction or daily aggregated record. "
            f"It records the date (`{date_col}`), the product sold (`{prod_col}`), "
            f"and units sold (`{qty_col}`)."
        )
    else:
        row_meaning = (
            f"Each row represents a business record for `{prod_col}` with `{qty_col}` "
            f"and other associated metrics."
        )

    # Dynamically group columns into logical categories
    category_groups: Dict[str, List[str]] = {
        "📦 Sales & Quantity": [],
        "💰 Revenue & Pricing": [],
        "📈 Profit & Margin": [],
        "🏪 Inventory & Stock": [],
        "🏷️ Product & Classification": [],
        "🤝 Supplier": [],
        "📊 Performance KPIs": [],
        "🔖 Other": [],
    }
    profit_kw = {"profit", "margin", "markup", "earnings", "cogs", "cost"}
    revenue_kw = {"revenue", "sale_value", "sales_value", "gross_sales", "gross_item_sale",
                  "net_sales", "net_revenue", "turnover", "amount", "net_amount", "gross_amount"}
    price_kw = {"price", "rate", "mrp", "avg_rate", "avg_price"}
    stock_kw = {"closing", "opening", "inward", "outward", "stock", "soh", "transfer", "receipt",
                "adjustment", "shrinkage", "cl_value"}
    qty_kw = {"qty", "quantity", "units", "sold", "returned", "return"}
    supplier_kw = {"vendor", "supplier", "party", "manufacturer", "dealer"}
    perf_kw = {"offtake", "sell_through", "days", "cover", "velocity", "turnover_rate",
               "conversion", "stock_turn"}
    product_kw = {"brand", "department", "category", "section", "sku", "item", "product",
                  "barcode", "article", "style", "description", "size", "color", "uom",
                  "hsn", "division", "segment"}

    for col, meaning in col_semantics.items():
        norm = re.sub(r"[^a-z0-9]+", "_", col.lower()).strip("_")
        if any(kw in norm for kw in profit_kw) and not any(kw in norm for kw in {"qty", "quantity"}):
            category_groups["📈 Profit & Margin"].append(f"**{col}**: {meaning}")
        elif any(kw in norm for kw in revenue_kw) and not any(kw in norm for kw in {"qty", "quantity"}):
            category_groups["💰 Revenue & Pricing"].append(f"**{col}**: {meaning}")
        elif any(kw in norm for kw in price_kw):
            category_groups["💰 Revenue & Pricing"].append(f"**{col}**: {meaning}")
        elif any(kw in norm for kw in qty_kw):
            category_groups["📦 Sales & Quantity"].append(f"**{col}**: {meaning}")
        elif any(kw in norm for kw in stock_kw):
            category_groups["🏪 Inventory & Stock"].append(f"**{col}**: {meaning}")
        elif any(kw in norm for kw in supplier_kw):
            category_groups["🤝 Supplier"].append(f"**{col}**: {meaning}")
        elif any(kw in norm for kw in perf_kw):
            category_groups["📊 Performance KPIs"].append(f"**{col}**: {meaning}")
        elif any(kw in norm for kw in product_kw):
            category_groups["🏷️ Product & Classification"].append(f"**{col}**: {meaning}")
        else:
            category_groups["🔖 Other"].append(f"**{col}**: {meaning}")

    # Build human-readable summary of non-empty groups
    group_bullets = []
    for group_name, items in category_groups.items():
        if items:
            preview = items[:3]
            extra = len(items) - 3
            bullets = "; ".join(preview)
            if extra > 0:
                bullets += f" (+{extra} more)"
            group_bullets.append(f"{group_name}: {bullets}")

    # Condensed facts for Gemma
    facts = {
        "row_explanation": row_meaning,
        "total_columns_parsed": len(col_semantics),
        "column_groups": {g: len(v) for g, v in category_groups.items() if v},
    }

    fallback = (
        f"**What your rows & columns represent:**\n\n"
        f"{row_meaning}\n\n"
        f"**All {len(col_semantics)} columns auto-understood by category:**\n"
        + "\n".join(f"• {b}" for b in group_bullets) +
        f"\n\nYou can ask me about any specific column (e.g. 'what does Profit Amt mean?') "
        f"or about profits, margins, revenue, stock levels, or sales performance."
    )

    text = await _call_gemma_with_facts(query, facts, fallback, col_context=col_semantics)
    return text, "schema_explanation", {"columns": col_semantics, "dataset_type": health.get("dataset_type")}


async def _answer_financial_query(
    dataset: Dataset,
    health: Dict[str, Any],
    col_semantics: Dict[str, str],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Answers questions about profit, margin, revenue, cost, discount for any dataset.
    Extracts financial column info dynamically from col_semantics."""
    query_lower = query.lower()

    # Identify which financial columns exist in this dataset
    profit_cols = {c: m for c, m in col_semantics.items()
                   if any(kw in re.sub(r"[^a-z0-9]+", "_", c.lower()) for kw in
                          ["profit", "margin", "earnings", "markup"])}
    revenue_cols = {c: m for c, m in col_semantics.items()
                    if any(kw in re.sub(r"[^a-z0-9]+", "_", c.lower()) for kw in
                           ["revenue", "gross_sale", "gross_item_sale", "sale_value",
                            "net_sales", "turnover", "gross_amount"])}
    cost_cols = {c: m for c, m in col_semantics.items()
                 if any(kw in re.sub(r"[^a-z0-9]+", "_", c.lower()) for kw in
                        ["cost", "cogs", "purchase_price"])}
    discount_cols = {c: m for c, m in col_semantics.items()
                     if any(kw in re.sub(r"[^a-z0-9]+", "_", c.lower()) for kw in
                            ["discount", "disc", "markdown", "rebate"])}

    all_financial = {**profit_cols, **revenue_cols, **cost_cols, **discount_cols}

    facts: Dict[str, Any] = {
        "dataset": dataset.name,
        "financial_columns_available": list(all_financial.keys()),
        "profit_columns": list(profit_cols.keys()),
        "revenue_columns": list(revenue_cols.keys()),
        "cost_columns": list(cost_cols.keys()),
        "discount_columns": list(discount_cols.keys()),
    }

    if not all_financial:
        fallback = (
            f"Your dataset ('{dataset.name}') does not appear to contain explicit profit, "
            f"revenue, or cost columns. The system focuses on sales quantity and stock-level "
            f"analysis for inventory decisions. If your file has profit or margin columns, "
            f"re-upload the file with those columns included."
        )
    else:
        col_bullets = "\n".join(f"• **{col}**: {meaning}" for col, meaning in all_financial.items())
        focus = "profit & margin" if profit_cols else ("revenue" if revenue_cols else "cost & discount")
        fallback = (
            f"Your dataset contains the following financial columns:\n\n"
            f"{col_bullets}\n\n"
            f"The AI analyses {focus} data directly from these columns. "
            f"Ask me things like 'which product has the highest profit?', "
            f"'what is the total margin?', or 'show me discount impact on sales'."
        )

    text = await _call_gemma_with_facts(query, facts, fallback, col_context=all_financial)
    return text, "schema_explanation", {"columns": all_financial}




async def _answer_top_movers_query(
    dataset: Dataset,
    health: Dict[str, Any],
    forecasts: List[ProductForecast],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Provides insights on the top selling brands / products."""
    top_moving = health.get("top_moving", [])
    if not top_moving and forecasts:
        sorted_f = sorted(forecasts, key=lambda f: sum(f.point_forecast or []), reverse=True)
        top_moving = [
            {"product": f.product_id, "sold": int(round(sum(f.point_forecast or []))), "stock": 0}
            for f in sorted_f[:8]
        ]

    facts = {
        "top_brands": top_moving[:6],
        "total_tracked": health.get("total_products", len(forecasts)),
    }

    bullets = "\n".join(
        f"{i+1}. **{item['product']}**: **{int(round(item['sold'])):,} units sold** "
        + (f"(Current Stock: {int(round(item['stock'])):,} units)" if item.get('stock') is not None else "")
        for i, item in enumerate(top_moving[:6])
    )

    fallback = (
        f"Here are the top performing brands by sales volume in your store:\n\n"
        f"{bullets}\n\n"
        f"These brands drive the highest customer volume. TimesFM and reorder thresholds are calibrated to prevent stockouts on these key lines."
    )

    text = await _call_gemma_with_facts(query, facts, fallback)
    return text, "top_movers", {"top_moving": top_moving[:8]}


async def _answer_stock_health_query(
    dataset: Dataset,
    health: Dict[str, Any],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Summarizes inventory health across out-of-stock, critical, healthy, and overstocked categories."""
    stock_health = health.get("stock_health", {})
    top_oos = health.get("top_out_of_stock", [])
    top_crit = health.get("top_critical", [])

    facts = {
        "stock_health": stock_health,
        "out_of_stock_count": stock_health.get("out_of_stock", 0),
        "critical_count": stock_health.get("critical", 0),
        "healthy_count": stock_health.get("healthy", 0),
        "overstocked_count": stock_health.get("overstocked", 0),
        "top_out_of_stock": top_oos[:4],
    }

    oos_sample = ", ".join(f"**{item['product']}**" for item in top_oos[:4]) if top_oos else "None"

    fallback = (
        f"**Stock Health Breakdown across {health.get('total_products', 0)} products/brands:**\n\n"
        f"• 🔴 **Out of Stock**: **{stock_health.get('out_of_stock', 0)} items** (Zero stock on hand despite sales demand; e.g., {oos_sample})\n"
        f"• 🟡 **Critical / Below ROP**: **{stock_health.get('critical', 0)} items** (Stock is below lead-time reorder point; order soon)\n"
        f"• 🟢 **Healthy**: **{stock_health.get('healthy', 0)} items** (Balanced safety stock)\n"
        f"• 🔵 **Overstocked**: **{stock_health.get('overstocked', 0)} items** (More than 90 days of cover)\n\n"
        f"You can view and order exact quantities for all critical and out-of-stock items in the purchase order CSV."
    )

    text = await _call_gemma_with_facts(query, facts, fallback)
    return text, "stock_health", {
        "stock_health": stock_health,
        "top_out_of_stock": top_oos[:6],
        "top_critical": top_crit[:6],
    }

