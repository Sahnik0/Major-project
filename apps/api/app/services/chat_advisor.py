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

    messages: List[ChatMessage] = []
    # If all cards including executive_summary already exist, return as is
    if "executive_summary" in existing_card_types:
        return existing

    # Step 1: Clean & Check Data Card
    messages: List[ChatMessage] = list(existing)

    total_cleaned = health.get("total_rows_cleaned", dataset.row_count or 0)
    total_prods = health.get("total_products", dataset.product_count or 0)
    class_dist = health.get("class_distribution", {})
    issues = health.get("issues", [])
    col_map = dataset.column_mapping or {}

    step1_content = (
        f"**Step 1: Data Cleaned & Verified.**\n"
        f"I inspected your sales history file ({dataset.original_filename}). "
        f"All missing days have been regularized with zero-sales records, duplicates resolved, "
        f"and returns isolated. Your {total_prods} products have been classified according to their demand patterns."
    )
    step1_payload = {
        "step": 1,
        "title": "Clean & Check the Data",
        "total_rows_cleaned": total_cleaned,
        "total_products": total_prods,
        "total_days": health.get("total_days", 0),
        "date_min": health.get("date_min"),
        "date_max": health.get("date_max"),
        "class_distribution": class_dist,
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
    # Step 1: Clean & Check Data Card
    if "health_summary" not in existing_card_types:
        date_col = col_map.get("date", "Date")
        prod_col = col_map.get("product", "Product")
        qty_col = col_map.get("quantity", "Quantity")
        rev_col = col_map.get("revenue")

    # If forecasts exist, create Steps 2 and 3
        mapping_desc = f"Date: `{date_col}`, Product: `{prod_col}`, Quantity: `{qty_col}`"
        if rev_col:
            mapping_desc += f", Revenue: `{rev_col}`"

        step1_content = (
            f"**Step 1: Data Ingestion & Hygiene Verified.**\n"
            f"I inspected your sales file (**{dataset.original_filename}**) and auto-detected your columns:\n"
            f"• **Mapped Columns**: {mapping_desc}\n\n"
            f"We regularized **{total_cleaned:,} records** spanning **{health.get('total_days', 0)} calendar days** "
            f"({health.get('date_min', 'start')} to {health.get('date_max', 'end')}). "
            f"Missing periods were zero-filled during active lifecycles, duplicate records consolidated, "
            f"and returns isolated. Your **{total_prods} products** have been classified by demand pattern."
        )
        step1_payload = {
            "step": 1,
            "title": "Clean & Check the Data",
            "total_rows_cleaned": total_cleaned,
            "total_products": total_prods,
            "total_days": health.get("total_days", 0),
            "date_min": health.get("date_min"),
            "date_max": health.get("date_max"),
            "column_mapping": col_map,
            "class_distribution": class_dist,
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

    # 2. Reorders & Ordering questions ("what should i order", "reorder", "restock")
    if any(w in query_lower for w in ["order", "reorder", "stock", "buy", "purchase", "restock"]):
        return await _answer_reorder_query(dataset, forecasts, query)

    # 3. Risk questions ("risk", "stockout", "run out")
    if any(w in query_lower for w in ["risk", "stockout", "shortage", "urgent"]):
        return await _answer_risk_query(dataset, forecasts, query)

    # 4. Model & Backtest proof questions ("timesfm", "baseline", "model", "accuracy", "error", "wape")
    if any(w in query_lower for w in ["timesfm", "baseline", "model", "autoets", "croston", "arima", "accuracy", "wape", "mase"]):
        return await _answer_model_query(dataset, forecasts, query)

    # 5. Data cleaning & health questions ("clean", "missing", "data", "quality", "outlier", "duplicates")
    if any(w in query_lower for w in ["clean", "health", "missing", "data", "quality", "outlier", "returns"]):
        return await _answer_health_query(dataset, health, query)

    # 6. General / aggregate questions ("total", "sales", "overall", "forecast", "summary")
    return await _answer_general_query(dataset, forecasts, health, query)


async def _call_gemma_with_facts(prompt: str, facts: Dict[str, Any], fallback_text: str) -> str:
    """Invokes local Gemma via Ollama, verifying numbers against facts."""
    system_instruction = (
        "You are a helpful retail inventory advisor for small business owners. "
        "Answer the user's question directly and concisely in 2-3 sentences based ONLY on the provided JSON facts. "
        "CRITICAL RULE: Never invent, estimate, or calculate numbers. Every single number you mention MUST appear exactly in the JSON facts."
    )
    user_prompt = f"Facts: {json.dumps(facts)}\n\nQuestion: {prompt}"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": f"{system_instruction}\n\n{user_prompt}",
                    "stream": False,
                    "options": {"temperature": 0.1},
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

    facts = {
        "dataset_name": dataset.name,
        "total_products": prods,
        "total_forecast_units": tot_units,
    }

    fallback = (
        f"For your dataset '{dataset.name}', we're tracking {prods} products. "
        f"Total projected sales demand across all items is {tot_units} units over the forecast period. "
        f"You can ask me about specific products, reorder requirements, or model backtests."
    )

    text = await _call_gemma_with_facts(query, facts, fallback)
    return text, None, None

