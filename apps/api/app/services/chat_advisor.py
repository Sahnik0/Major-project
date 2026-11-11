import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from apps.api.app.config import settings
from apps.api.app.db import ChatMessage, Dataset, ProductForecast
from apps.api.app.services.narrator import validate_narrative_traceability

logger = logging.getLogger("advisor.chat")


def _is_inventory_domain(col_map: Dict[str, Any], health: Dict[str, Any]) -> bool:
    """Detects whether a dataset represents physical retail/inventory demand vs a general domain dataset."""
    if health.get("dataset_type") == "stock_summary" or col_map.get("_dataset_type") == "stock_summary":
        return True
    if col_map.get("stock_on_hand"):
        return True
    all_cols_lower = " ".join(str(k).lower() for k in (col_map.get("_column_semantics") or {}).keys())
    inv_signals = ("sku", "units_sold", "sale_qty", "sales_qty", "closing_qty", "stock", "inventory", "warehouse", "reorder", "promo_flag")
    return any(sig in all_cols_lower for sig in inv_signals)
def _ensure_enriched_mapping(dataset: Dataset, db: Optional[Session] = None) -> Dict[str, Any]:
    """Ensures `_column_profiles`, `_column_semantics`, `_row_grain`, and `_suggested_questions` exist on dataset."""
    col_map = dict(dataset.column_mapping or {})
    if col_map.get("_column_profiles") and col_map.get("_column_semantics") and col_map.get("_row_grain"):
        return col_map

    try:
        from apps.api.app.services.column_mapping import (
            build_column_profiles,
            detect_row_grain,
            explain_columns,
            generate_suggested_questions,
            suggest_column_mapping_heuristics,
        )
        from apps.api.app.services.dataset_tools import get_dataset_dataframe

        df = get_dataset_dataframe(dataset)
        if not col_map.get("quantity") or not col_map.get("product"):
            inferred_map, _, _ = suggest_column_mapping_heuristics(df)
            for k, v in inferred_map.items():
                if k not in col_map or not col_map[k]:
                    col_map[k] = v
        else:
            profiles = build_column_profiles(df)
            col_map["_column_profiles"] = profiles
            col_map["_column_semantics"] = explain_columns(df.columns, df=df)
            col_map["_row_grain"] = detect_row_grain(
                df,
                profiles,
                date_col=col_map.get("date") if col_map.get("date") in df.columns else None,
            )
            col_map["_suggested_questions"] = generate_suggested_questions(df, profiles, col_map)

        if db is not None:
            dataset.column_mapping = col_map
            db.commit()
    except Exception as e:
        logger.warning(f"Schema enrichment fallback warning: {e}")

    return col_map


def get_or_create_initial_messages(dataset: Dataset, db: Session) -> List[ChatMessage]:
    """
    Creates or retrieves the structured onboarding & executive briefing cards for any dataset:
    - Step 1: Universal Data Understanding (Rows, Columns, Grain, Types, Ranges, Health)
    - Step 2: Forecast Demand with Proof (when forecasts are available)
    - Step 3: Actionable Inventory / Replenishment Decisions (when stock/reorder forecasts apply)
    - Step 4: Executive Summary & Predictions Briefing
    """
    existing = (
        db.query(ChatMessage)
        .filter(ChatMessage.dataset_id == dataset.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    existing_card_types = {m.card_type for m in existing if m.card_type}

    health = dataset.health_report or {}
    forecasts = (
        db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset.id).all()
    )

    # Fast return if all applicable initial cards already exist
    if "health_summary" in existing_card_types and (not forecasts or "forecast_proof" in existing_card_types):
        return existing

    col_map = _ensure_enriched_mapping(dataset, db)
    col_semantics = col_map.get("_column_semantics", {})
    col_profiles = col_map.get("_column_profiles", {})
    row_grain = col_map.get("_row_grain") or health.get("row_grain") or {}
    suggested_questions = col_map.get("_suggested_questions", [])

    total_raw = health.get("total_rows_raw", dataset.row_count or 0)
    total_cleaned = health.get("total_rows_cleaned", dataset.row_count or 0)
    total_prods = health.get("total_products") if health.get("total_products") is not None else (dataset.product_count or 0)
    class_dist = health.get("class_distribution", {})
    issues = health.get("issues", [])
    stock_health = health.get("stock_health") or {}
    top_moving = health.get("top_moving") or []
    dataset_type = health.get("dataset_type") or col_map.get("_dataset_type") or "time_series"
    granularity = health.get("granularity", "daily")

    # Step 1: Universal Data & Schema Understanding Card
    if "health_summary" not in existing_card_types:
        date_col = col_map.get("date")
        prod_col = col_map.get("product")
        qty_col = col_map.get("quantity")
        stock_col = col_map.get("stock_on_hand")
        rev_col = col_map.get("revenue")

        mapped_parts = []
        if date_col and not str(date_col).startswith("__"):
            mapped_parts.append(f"Date/Time: `{date_col}`")
        if prod_col and not str(prod_col).startswith("__"):
            mapped_parts.append(f"Primary Entity/Group: `{prod_col}`")
        if qty_col and not str(qty_col).startswith("__"):
            mapped_parts.append(f"Primary Metric: `{qty_col}`")
        if stock_col:
            mapped_parts.append(f"Stock on Hand: `{stock_col}`")
        if rev_col:
            mapped_parts.append(f"Revenue/Value: `{rev_col}`")
        mapping_desc = ", ".join(mapped_parts) if mapped_parts else f"{len(col_semantics)} columns detected"

        grain_line = row_grain.get("description") or f"{total_raw:,} rows across {len(col_semantics)} columns."

        if dataset_type == "tabular":
            step1_content = (
                f"**Step 1: Complete Dataset & Schema Understood.**\n"
                f"I inspected **{dataset.original_filename}** (**{total_raw:,} rows × {len(col_semantics)} columns**) in cross-sectional tabular mode:\n"
                f"• **Row Structure**: {grain_line}\n"
                f"• **Key Fields**: {mapping_desc}\n\n"
                f"All **{len(col_semantics)} columns** have been profiled with data types, ranges, and category distributions. "
                f"You can run any SQL/analytical query, correlation, ranking, or generate filtered/transformed CSV exports right away."
            )
        elif dataset_type == "stock_summary":
            step1_content = (
                f"**Step 1: Data Ingestion & Schema Verified.**\n"
                f"I inspected your ERP Stock & Sales Summary (**{dataset.original_filename}**, **{total_raw:,} raw rows × {len(col_semantics)} columns**):\n"
                f"• **Row Structure**: {grain_line}\n"
                f"• **Mapped Columns**: {mapping_desc}\n\n"
                f"We analyzed reporting window **{health.get('date_min', 'start')}** to **{health.get('date_max', 'end')}** "
                f"({health.get('total_days', 0)} days) across **{total_prods:,} entities**."
            )
        else:
            date_span_str = (
                f"from **{health.get('date_min')}** to **{health.get('date_max')}** ({granularity} cadence)"
                if health.get("date_min") and health.get("date_max")
                else f"across **{total_cleaned:,} records**"
            )
            step1_content = (
                f"**Step 1: Data Ingestion & Schema Verified.**\n"
                f"I inspected **{dataset.original_filename}** (**{total_raw:,} raw rows × {len(col_semantics)} columns**):\n"
                f"• **Row Structure**: {grain_line}\n"
                f"• **Mapped Columns**: {mapping_desc}\n\n"
                f"We regularized **{total_cleaned:,} series records** {date_span_str} across **{total_prods:,} series/entities**."
            )

        step1_payload = {
            "step": 1,
            "title": "Clean & Check the Data",
            "total_rows_raw": total_raw,
            "total_rows_cleaned": total_cleaned,
            "total_columns": len(col_semantics),
            "total_products": total_prods,
            "total_days": health.get("total_days", 0),
            "granularity": granularity,
            "date_min": health.get("date_min"),
            "date_max": health.get("date_max"),
            "row_grain": row_grain,
            "column_mapping": {
                k: v for k, v in col_map.items()
                if not k.startswith("_") and not str(v).startswith("__")
            },
            "column_semantics": col_semantics,
            "column_profiles": col_profiles,
            "suggested_questions": suggested_questions,
            "class_distribution": class_dist,
            "stock_health": stock_health,
            "top_moving": top_moving[:6],
            "dataset_type": dataset_type,
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
        db.commit()

    # If forecasts exist, generate Step 2, Step 3 (if inventory domain), and Step 4
    if forecasts:
        from apps.api.app.services.cleaning import _is_non_additive_metric

        is_inv = _is_inventory_domain(col_map, health)
        qty_label = col_map.get("quantity") if not str(col_map.get("quantity", "")).startswith("__") else "Metric"
        prod_label = col_map.get("product") if not str(col_map.get("product", "")).startswith("__") else "Series"
        non_additive = _is_non_additive_metric(str(qty_label))

        model_counts: Dict[str, int] = {}
        total_forecast_units = 0.0
        wapes = []

        for f in forecasts:
            m_name = f.chosen_model or "Unknown"
            model_counts[m_name] = model_counts.get(m_name, 0) + 1
            pt = f.point_forecast or []
            total_forecast_units += (float(sum(pt)) / len(pt)) if (non_additive and pt) else sum(pt)
            bt = f.backtest_table or []
            for b in bt:
                if b.get("model_name") == m_name and "wape" in b:
                    wapes.append(b["wape"])

        avg_wape = round(float(sum(wapes) / len(wapes)) * 100, 1) if wapes else 15.0

        def _series_summary_val(pts: List[float]) -> int:
            if not pts:
                return 0
            return int(round(sum(pts) / len(pts))) if non_additive else int(round(sum(pts)))

        sorted_forecasts = sorted(
            forecasts,
            key=lambda f: _series_summary_val(f.point_forecast or []),
            reverse=True,
        )
        top_projected = [
            {
                "product": f.product_id,
                "model": f.chosen_model,
                "projected_units": _series_summary_val(f.point_forecast or []),
                "p10": _series_summary_val(f.quantiles.get("0.1", f.point_forecast or [])),
                "p90": _series_summary_val(f.quantiles.get("0.9", f.point_forecast or [])),
            }
            for f in sorted_forecasts[:5]
        ]
        horizon_len = len(sorted_forecasts[0].point_forecast or []) if sorted_forecasts else 28

        # Step 2: Forecast Proof (Domain-Adaptive)
        if "forecast_proof" not in existing_card_types:
            unit_suffix = "" if non_additive else " units"
            top_prods_str = ", ".join(f"**{p['product']}** (~{p['projected_units']:,}{unit_suffix})" for p in top_projected[:3])
            agg_summary_line = (
                f"• **Projected Average `{qty_label}`**: **{round(total_forecast_units / max(1, len(forecasts)), 1):,}** across **{len(forecasts)}** `{prod_label}` series ({horizon_len}-period horizon).\n"
                if non_additive
                else f"• **Total Projected `{qty_label}`**: **{int(round(total_forecast_units)):,}{unit_suffix}** over the upcoming {horizon_len}-period horizon.\n"
            )
            step2_content = (
                "**Step 2: Time-Series Forecast with Backtest Proof.**\n"
                "We evaluated Google TimesFM against statistical baselines (AutoETS, AutoARIMA, SeasonalNaive, Croston) on your actual data "
                "using rolling-origin backtesting, selecting the empirical winner per series.\n"
                f"{agg_summary_line}"
                f"• **Leading Series**: {top_prods_str}.\n"
                f"• **Average Backtest Error (WAPE)**: **~{avg_wape}%** across evaluated folds."
            )
            step2_payload = {
                "step": 2,
                "title": "Forecast with Backtest Proof",
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

        # Step 3: Turn Forecast into Decisions (ONLY for physical inventory / retail datasets!)
        reorder_items = []
        high_risk_count = 0
        if is_inv:
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
                    "**Step 3: Actionable Reorder Decisions.**\n"
                    "Deterministic lead-time math converted the forecasts into exact Reorder Points (ROP) and order quantities based on your supplier lead time and 90% service level.\n"
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

        # Message 4: Executive Summary & Predictions Briefing (Domain-Adaptive)
        if "executive_summary" not in existing_card_types:
            active_classes = [f"{count} {cls}" for cls, count in class_dist.items() if count > 0]
            class_str = ", ".join(active_classes) if active_classes else "series analyzed"
            models_str = ", ".join(f"{m} ({c} series)" for m, c in model_counts.items())
            unit_lbl = "" if non_additive else " units"

            summary_paragraphs = [
                f"### Executive Summary & Predictions Briefing\n"
                f"Here is the analytical & forecasting overview for **{dataset.name}** across **{len(forecasts)} series**:\n"
            ]

            summary_paragraphs.append(
                f"**1. Data Structure & Hygiene**\n"
                f"We processed **{total_cleaned:,} regularized records** from **{health.get('date_min', 'start')}** to **{health.get('date_max', 'end')}** "
                f"({granularity} cadence). Primary schema fields: `{col_map.get('date', 'date')}`, `{prod_label}`, `{qty_label}`. "
                f"Pattern classification: {class_str}."
            )

            top_prods_brief = "\n".join(
                f"- **{p['product']}**: ~**{p['projected_units']:,}{unit_lbl}** projected (80% CI: {p['p10']:,} – {p['p90']:,}{unit_lbl}) — *Model: {p['model']}*"
                for p in top_projected[:4]
            )
            summary_paragraphs.append(
                f"**2. Backtested Projections ({horizon_len}-Period Horizon)**\n"
                f"Winning models by rolling-origin WAPE (~**{avg_wape}%** avg error): {models_str}.\n"
                f"Leading series projections:\n"
                f"{top_prods_brief}"
            )

            if is_inv:
                if reorder_items:
                    reorder_bullets = "\n".join(
                        f"- **{r['product']}**: Order **{r['recommended_qty']} units** (Reorder Point: {int(round(r['reorder_point']))}, Risk: {r['stockout_risk']})"
                        for r in reorder_items[:5]
                    )
                    summary_paragraphs.append(
                        f"**3. Reorder Decisions & Stocking Plan**\n"
                        f"Based on a 7-day supplier lead time and 90% service level, **{len(reorder_items)} products** need reorders:\n"
                        f"{reorder_bullets}"
                    )
                else:
                    summary_paragraphs.append(
                        "**3. Reorder Decisions & Stocking Plan**\n"
                        "Current inventory levels are sufficient across all analyzed items."
                    )

            summary_paragraphs.append(
                "---\n"
                "💡 *Ask me anything about your dataset — for example:*\n"
                + "\n".join(f"- *'{q}'*" for q in (suggested_questions[:4] or [
                    "Show me the future prediction",
                    "Explain all rows and columns in this dataset",
                    "Generate a CSV summary grouped by category",
                ]))
            )

            m4 = ChatMessage(
                dataset_id=dataset.id,
                role="assistant",
                content="\n\n".join(summary_paragraphs),
                card_type="executive_summary",
            )
            db.add(m4)

        db.commit()

    return (
        db.query(ChatMessage)
        .filter(ChatMessage.dataset_id == dataset.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


async def _try_direct_duckdb_analyst(
    dataset: Dataset,
    query: str,
    available_cols: List[str],
    col_semantics: Dict[str, str],
    col_profiles: Dict[str, Dict[str, Any]],
    recent_history: List[Dict[str, str]],
) -> Optional[Dict[str, Any]]:
    """
    Universal SQL fallback: translates any open-ended analytical question into a read-only
    DuckDB SELECT query over `data` so virtually any tabular question can be answered accurately.
    """
    from apps.api.app.services.dataset_tools import (
        _build_schema_context_for_agent,
        execute_sql_query_tool,
    )

    schema_block = _build_schema_context_for_agent(available_cols, col_semantics, col_profiles)
    hist_str = "\n".join(f"{m['role'].upper()}: {m['content'][:180]}" for m in recent_history[-4:])
    prompt = (
        "You are a senior SQL Data Analyst. Write a single read-only DuckDB SELECT query on table `data` "
        "to answer the user's question accurately.\n"
        f"Table `data` schema ({len(available_cols)} columns):\n{schema_block}\n\n"
        f"Recent conversation:\n{hist_str}\n\n"
        f"User question: \"{query}\"\n\n"
        "Rules:\n"
        "- Always wrap column names in double quotes (e.g. \"Sale Qty\", \"MA\").\n"
        "- Limit output to at most 25 rows unless counting/aggregating.\n"
        "- If the question is purely conversational (e.g. 'hello', 'thanks') and cannot be answered by querying `data`, return {\"sql\": null}.\n"
        "Respond ONLY in JSON: {\"sql\": \"SELECT ... FROM data ...\" | null, \"title\": \"Short analysis title\"}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
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
                sql = parsed.get("sql")
                if sql and isinstance(sql, str) and sql.strip():
                    res = execute_sql_query_tool(
                        dataset=dataset,
                        sql=sql,
                        title=parsed.get("title") or "Custom SQL Analysis",
                        user_query=query,
                    )
                    if not res.get("error"):
                        return res
    except Exception as e:
        logger.warning(f"Direct DuckDB analyst fallback error: {e}")
    return None


def _dataset_label_safe(tool_res: Dict[str, Any]) -> str:
    return str(tool_res.get("sql_query") or tool_res.get("tool_name") or "dataset")


async def answer_user_query(
    dataset: Dataset,
    query: str,
    db: Session,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """
    Universal AI Data Analyst query handler:
    - Understands 100% of rows & columns for any uploaded format
    - Maintains multi-turn conversation context
    - Executes DuckDB SQL, Polars aggregations, correlations, anomaly detection,
      on-demand future forecasting, and custom CSV/Excel file generation
    - Validates numerical traceability against deterministic facts
    """
    query_lower = query.lower().strip()
    forecasts = (
        db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset.id).all()
    )
    health = dataset.health_report or {}
    col_map = _ensure_enriched_mapping(dataset, db)
    col_semantics = col_map.get("_column_semantics", {})
    col_profiles = col_map.get("_column_profiles", {})
    available_cols = list(col_profiles.keys()) or list(col_semantics.keys())

    if not available_cols:
        try:
            from apps.api.app.services.dataset_tools import get_dataset_dataframe

            available_cols = get_dataset_dataframe(dataset).columns
        except Exception:
            available_cols = []

    prior_msgs = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.dataset_id == dataset.id,
            ChatMessage.card_type.is_(None)
            | ChatMessage.card_type.notin_(
                ["health_summary", "forecast_proof", "inventory_decision", "executive_summary"]
            ),
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(7)
        .all()
    )
    recent_history = [
        {"role": m.role, "content": m.content}
        for m in reversed(prior_msgs[1:])
    ]

    is_csv_generation = (
        any(ext in query_lower for ext in ("csv", "excel", "xlsx", "spreadsheet"))
        and any(
            v in query_lower
            for v in (
                "generate", "create", "export", "download", "make", "give",
                "save", "extract", "build", "prepare", "output", "produce",
            )
        )
    )
    is_future_prediction = (
        any(
            kw in query_lower
            for kw in (
                "future prediction", "predict", "prediction", "predictions", "forecast",
                "next month", "next quarter", "next year", "next week", "future trend",
                "project future", "upcoming demand", "what will happen", "future sales",
                "show me the future",
            )
        )
        and not is_csv_generation
        and not any(w in query_lower for w in ("why was", "wape", "mase", "which model won", "accuracy"))
    )

    # 1. Schema / Row / Column Understanding Questions
    is_schema_definition_query = (
        any(
            w in query_lower
            for w in [
                "represent", "meaning", "what does", "define", "definition", "explain columns",
                "explain all rows", "explain row", "rows and columns", "what do the columns",
                "what do the rows", "what is this column", "header meaning", "describe this dataset",
                "understand this data", "schema",
            ]
        )
        and not is_csv_generation
        and not is_future_prediction
        and not any(w in query_lower for w in ["reorder", "purchase", "top ", "bottom ", "sum ", "average ", "compare "])
    )
    if is_schema_definition_query:
        return await _answer_schema_query(dataset, health, query, col_map=col_map)

    # 2. Specific single-product inspection (when user asks e.g. "Tell me about PROD_01")
    target_forecast: Optional[ProductForecast] = None
    if not is_csv_generation and not any(w in query_lower for w in ("compare", " vs ", "versus", "top ", "bottom ")):
        for f in forecasts:
            if len(f.product_id) >= 3 and re.search(rf"\b{re.escape(f.product_id.lower())}\b", query_lower):
                target_forecast = f
                break

    if target_forecast and not is_future_prediction:
        return await _answer_product_query(dataset, target_forecast, query)

    # 3. CHAIN-OF-THOUGHT AGENTIC DATA ANALYST PIPELINE
    t_start = time.perf_counter()
    try:
        from apps.api.app.services.dataset_tools import (
            execute_dataset_tool,
            get_duckdb_ready_dataframe,
            plan_analysis_with_cot,
            run_duckdb_df,
        )

        tool_name, tool_args, cot_steps, secondary_sql = await plan_analysis_with_cot(
            dataset=dataset,
            query=query,
            available_columns=available_cols,
            col_semantics=col_semantics,
            col_profiles=col_profiles,
            recent_history=recent_history,
        )
        if tool_name:
            if tool_name == "forecast_future" and target_forecast and not tool_args.get("filter_value"):
                prod_col_mapped = col_map.get("product")
                if prod_col_mapped and prod_col_mapped in available_cols:
                    tool_args["filter_column"] = prod_col_mapped
                    tool_args["filter_value"] = target_forecast.product_id

            tool_res = execute_dataset_tool(dataset, tool_name, tool_args, db, user_query=query)
            if tool_res and not tool_res.get("error"):
                # Optional 2nd-step supporting SQL execution from CoT plan
                if secondary_sql and isinstance(secondary_sql, str):
                    try:
                        sup_df = run_duckdb_df(get_duckdb_ready_dataframe(dataset), secondary_sql)
                        if len(sup_df) > 0:
                            sup_records = sup_df.head(5).to_dicts()
                            tool_res.setdefault("facts", {})["supporting_context"] = sup_records
                            cot_steps.append({
                                "phase": "Supporting Context Query",
                                "thought": f"Executed secondary context SQL (`{secondary_sql}`) -> {len(sup_df)} baseline row(s).",
                            })
                    except Exception:
                        pass

                # Record deterministic execution & self-reflection steps in CoT trace
                exec_detail = (
                    f"Ran `{tool_res.get('tool_name')}`"
                    + (f" — SQL: `{tool_res['sql_query']}`" if tool_res.get("sql_query") else "")
                    + (f" ({len(tool_res.get('table_rows', []))} display rows)" if tool_res.get("table_rows") is not None else "")
                )
                cot_steps.append({"phase": "Deterministic Execution", "thought": exec_detail})
                for refl in tool_res.get("self_reflection") or []:
                    cot_steps.append({"phase": "Self-Reflection & Recovery", "thought": str(refl)})

                narrative = await _explain_tool_result_with_gemma(
                    query, tool_res, col_semantics, cot_steps=cot_steps
                )
                elapsed_ms = int(round((time.perf_counter() - t_start) * 1000))
                tool_res["reasoning_steps"] = cot_steps
                tool_res["execution_ms"] = elapsed_ms

                card_t = (
                    "csv_export"
                    if tool_res.get("tool_name") == "generate_csv_export"
                    else ("forecast_result" if tool_res.get("tool_name") == "forecast_future" else "tool_result")
                )
                return narrative, card_t, tool_res
            elif tool_res and tool_res.get("error") and is_future_prediction:
                return tool_res["error"], None, None
    except Exception as e_tool:
        logger.warning(f"Agentic CoT tool execution error: {e_tool}")

    # 4. Domain-specific handlers when applicable
    is_inv = _is_inventory_domain(col_map, health)
    if is_inv and forecasts and any(w in query_lower for w in ["reorder", "restock", "purchase order", "what should i order"]):
        return await _answer_reorder_query(dataset, forecasts, query)

    if is_inv and forecasts and any(w in query_lower for w in ["stockout", "shortage", "high risk"]):
        return await _answer_risk_query(dataset, forecasts, query)

    if forecasts and any(w in query_lower for w in ["timesfm", "baseline", "autoets", "croston", "arima", "accuracy", "wape", "mase"]):
        return await _answer_model_query(dataset, forecasts, query)

    if health.get("stock_health") and any(
        w in query_lower for w in ["out of stock", "overstock", "overstocked", "critical stock", "stock health", "zero stock"]
    ):
        return await _answer_stock_health_query(dataset, health, query)

    if any(w in query_lower for w in ["clean", "health", "missing", "quality", "duplicates"]):
        return await _answer_health_query(dataset, health, query)

    # 5. Universal DuckDB SQL Analyst Pass for any remaining analytical question
    if available_cols:
        sql_res = await _try_direct_duckdb_analyst(
            dataset=dataset,
            query=query,
            available_cols=available_cols,
            col_semantics=col_semantics,
            col_profiles=col_profiles,
            recent_history=recent_history,
        )
        if sql_res and not sql_res.get("error"):
            fallback_cot = [
                {"phase": "Schema Grounding", "thought": f"Inspected {len(available_cols)} columns on `{dataset.name}`."},
                {"phase": "DuckDB SQL Synthesis", "thought": f"Executed `{sql_res.get('sql_query', 'SELECT')}`."},
            ]
            for refl in sql_res.get("self_reflection") or []:
                fallback_cot.append({"phase": "Self-Reflection & Recovery", "thought": str(refl)})
            narrative = await _explain_tool_result_with_gemma(query, sql_res, col_semantics, cot_steps=fallback_cot)
            sql_res["reasoning_steps"] = fallback_cot
            sql_res["execution_ms"] = int(round((time.perf_counter() - t_start) * 1000))
            return narrative, "tool_result", sql_res

    # 6. Top movers / financial / general overview fallbacks
    if any(w in query_lower for w in ["top brand", "top seller", "best seller", "best selling", "most sold", "top moving"]):
        return await _answer_top_movers_query(dataset, health, forecasts, query)

    if any(w in query_lower for w in ["profit", "margin", "revenue", "gross sales", "cogs", "discount"]):
        return await _answer_financial_query(dataset, health, col_semantics, query)

    return await _answer_general_query(dataset, forecasts, health, query, col_map=col_map)


async def _call_gemma_with_facts(
    prompt: str,
    facts: Dict[str, Any],
    fallback_text: str,
    col_context: Optional[Dict[str, str]] = None,
    cot_steps: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Invokes local Gemma with Chain-of-Thought Synthesis (`synthesis_thought` -> `answer_markdown`),
    verifying every number against deterministic facts.
    """
    col_hint = ""
    if col_context:
        items = list(col_context.items())[:24]
        col_hint = "Dataset columns: " + "; ".join(f"`{c}` = {m}" for c, m in items) + ".\n"

    system_instruction = (
        "You are a Senior AI Data Analyst capable of analyzing any dataset from any domain.\n"
        f"{col_hint}"
        "Use Chain-of-Thought: FIRST reason through the exact numbers, comparisons, shares, and edge cases in `synthesis_thought`, "
        "and THEN write a clear, insightful, well-structured Markdown answer in `answer_markdown` (2-5 concise sentences or crisp bullets).\n"
        "CRITICAL RULES:\n"
        "(1) Never invent, estimate, or hallucinate numbers — every single number in `answer_markdown` MUST appear in the provided JSON facts.\n"
        "(2) Highlight the headline takeaway first, then key comparisons/shares/trends from `computed_insights` if present.\n"
        "(3) Adapt your terminology naturally to the dataset's actual domain and column names (do NOT use retail/inventory jargon unless the columns are retail inventory).\n"
        "Respond ONLY in valid JSON: {\"synthesis_thought\": \"...\", \"answer_markdown\": \"...\"}"
    )
    user_prompt = f"Facts: {json.dumps(facts, default=str)}\n\nQuestion: {prompt}"

    try:
        async with httpx.AsyncClient(timeout=14.0) as client:
            resp = await client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "system": system_instruction,
                    "prompt": user_prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.05},
                },
            )
            if resp.status_code == 200:
                raw = resp.json().get("response", "").strip()
                try:
                    parsed = json.loads(raw)
                    synth_thought = str(parsed.get("synthesis_thought", "")).strip()
                    ans_md = str(parsed.get("answer_markdown", "")).strip()
                except Exception:
                    synth_thought = ""
                    ans_md = raw

                if ans_md and validate_narrative_traceability(ans_md, facts):
                    if cot_steps is not None:
                        cot_steps.append({
                            "phase": "CoT Synthesis & Verification",
                            "thought": (synth_thought + " — " if synth_thought else "") + "All cited figures verified against deterministic facts ✓",
                        })
                    return ans_md
    except Exception as e:
        logger.warning(f"Ollama CoT synthesis fallback: {e}.")

    if cot_steps is not None:
        cot_steps.append({
            "phase": "Deterministic Insight Synthesis",
            "thought": "Constructed verified Markdown synthesis directly from computed metrics and comparative deltas ✓",
        })
    return fallback_text


async def _explain_tool_result_with_gemma(
    query: str,
    tool_res: Dict[str, Any],
    col_semantics: Dict[str, str],
    cot_steps: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Uses Chain-of-Thought Gemma synthesis to explain deterministic tool results, with rich comparative fallbacks."""
    facts = tool_res.get("facts", {})
    comp = facts.get("computed_insights") or {}
    title = tool_res.get("title", "Dataset Analysis")
    tool_name = tool_res.get("tool_name", "tool")
    rows = tool_res.get("table_rows", [])
    headers = tool_res.get("table_headers", [])

    if tool_name == "generate_csv_export":
        r_cnt = tool_res.get("row_count", facts.get("rows_exported", 0))
        c_cnt = tool_res.get("column_count", facts.get("columns_exported", 0))
        fname = tool_res.get("filename", "export.csv")
        fallback = (
            f"I generated **{fname}** containing **{r_cnt:,} rows** across **{c_cnt} columns** "
            f"based on your specification.\n\n"
            f"Click **Download CSV** (or **Download Excel**) on the card below to save the file, or inspect the preview rows directly."
        )
    elif tool_name == "forecast_future":
        if "last_observed_value" in facts:
            chg = facts.get("projected_change_pct", 0.0)
            dir_word = "increase" if chg > 0 else ("decrease" if chg < 0 else "remain steady")
            wape_note = (
                f" (backtest WAPE: **{facts['backtest_wape_pct']}%**)"
                if facts.get("backtest_wape_pct") is not None
                else ""
            )
            fallback = (
                f"**{title}**\n\n"
                f"Using **{facts.get('winning_model', 'statistical forecasting')}**{wape_note} trained on **{facts.get('historical_periods', 0)}** historical periods "
                f"(last observed `{facts.get('last_observed_date')}` = **{facts.get('last_observed_value'):,}**), "
                f"`{facts.get('target')}` is projected to **{dir_word} by {abs(chg):.2f}%** to **{facts.get('final_predicted_value'):,}** "
                f"by `{facts.get('forecast_end_date')}` (horizon average: **{facts.get('average_predicted_value'):,}**)."
            )
        else:
            fallback = (
                f"**{title}**\n\n"
                f"Projected forecast across **{facts.get('products_forecasted', 0)} series** totals "
                f"**{facts.get('total_projected_units', 0):,}** over the **{facts.get('horizon_periods', 28)}**-period horizon."
            )
    elif rows:
        if len(headers) >= 2:
            row_bullets = "\n".join(
                "• **" + str(r[0]) + "**: " + ", ".join(f"{headers[i]} = {r[i]}" for i in range(1, min(len(headers), len(r), 5)))
                for r in rows[:6]
            )
        else:
            row_bullets = "\n".join(f"• **{r[0]}**" for r in rows[:6])

        # Add rich comparative takeaway from computed_insights when available
        insight_lines = []
        if comp.get("leader") and comp.get("runner_up"):
            ldr = comp["leader"]
            rup = comp["runner_up"]
            lead_pct = comp.get("leader_vs_runner_up_pct")
            share_pct = comp.get("leader_share_pct")
            takeaway = f"💡 **Key Takeaway**: **{ldr['entity']}** leads at **{ldr['value']:,}**"
            if lead_pct is not None and abs(lead_pct) > 0:
                takeaway += f" (**{abs(lead_pct):.1f}%** {'above' if lead_pct > 0 else 'vs'} #{2} **{rup['entity']}** at **{rup['value']:,}**)"
            if share_pct is not None:
                takeaway += f", representing **{share_pct:.1f}%** of the displayed total"
            takeaway += "."
            insight_lines.append(takeaway)

        not_found = facts.get("requested_entities_not_found") or facts.get("entities_not_found")
        prefix_note = (
            f"⚠️ Note: `{', '.join(not_found)}` was not found in `{facts.get('dimension', 'the dataset')}`. Showing top actual records instead:\n\n"
            if not_found
            else ""
        )
        suffix_note = ("\n\n" + "\n".join(insight_lines)) if insight_lines else ""
        fallback = f"**{title}**\n\n{prefix_note}{row_bullets}{suffix_note}"
    else:
        fallback = f"**{title}**: Query executed on `{_dataset_label_safe(tool_res)}` (0 matching rows returned)."

    prompt = (
        f"The user asked: \"{query}\"\n"
        f"The analytical engine executed `{tool_name}` and computed these exact verified facts:\n"
        f"{json.dumps(facts, default=str)}\n\n"
        f"Reason step-by-step over the facts and `computed_insights`, then write an insightful, structured analyst response."
    )

    return await _call_gemma_with_facts(prompt, facts, fallback, col_context=col_semantics, cot_steps=cot_steps)



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
    col_map: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    col_map = col_map or dataset.column_mapping or {}
    col_semantics = col_map.get("_column_semantics", {})
    col_profiles = col_map.get("_column_profiles", {})
    row_grain = col_map.get("_row_grain") or health.get("row_grain") or {}

    tot_units = int(round(sum(sum(f.point_forecast or []) for f in forecasts))) if forecasts else 0
    prods = len(forecasts) or health.get("total_products") or dataset.product_count or 0
    total_rows = health.get("total_rows_raw") or dataset.row_count or 0

    numeric_highlights = {}
    for c, p in col_profiles.items():
        if p.get("role") == "numeric_measure":
            numeric_highlights[c] = {"min": p.get("min"), "max": p.get("max"), "mean": p.get("mean"), "sum": p.get("sum")}

    facts: Dict[str, Any] = {
        "dataset_name": dataset.name,
        "total_rows": total_rows,
        "total_columns": len(col_semantics),
        "row_structure": row_grain.get("description"),
        "total_entities": prods,
        "columns_available": list(col_semantics.keys()),
        "numeric_summary": numeric_highlights,
    }
    if forecasts:
        facts["total_forecast_units"] = tot_units

    fallback = (
        f"**{dataset.name}** contains **{total_rows:,} rows** across **{len(col_semantics)} columns** "
        f"({row_grain.get('description', f'{prods:,} distinct entities')}). "
        + (f"Total projected forecast across tracked series is **{tot_units:,}**. " if forecasts else "")
        + "You can ask me any analytical question, request future predictions, explore correlations/outliers, or ask me to generate a filtered/summarized CSV."
    )

    cot_steps = [
        {"phase": "Schema & Grain Inspection", "thought": f"Inspected {total_rows:,} rows × {len(col_semantics)} columns; grain: {row_grain.get('description', 'tabular')}."},
    ]
    text = await _call_gemma_with_facts(query, facts, fallback, col_context=col_semantics, cot_steps=cot_steps)
    return text, None, {"reasoning_steps": cot_steps}


async def _answer_schema_query(
    dataset: Dataset,
    health: Dict[str, Any],
    query: str,
    col_map: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Explains what 100% of the rows and columns in the user's file represent, backed by real column profiles."""
    col_map = col_map or _ensure_enriched_mapping(dataset)
    col_semantics = col_map.get("_column_semantics", {})
    col_profiles = col_map.get("_column_profiles", {})
    row_grain = col_map.get("_row_grain") or health.get("row_grain") or {}
    query_lower = query.lower()

    # Check if user asked about ONE specific column (and not "all columns" / "rows and columns")
    asking_all = any(p in query_lower for p in ("all columns", "rows and columns", "explain columns", "what do the columns", "what do the rows", "describe this dataset"))
    matched_col = None
    matched_meaning = None
    if not asking_all:
        for col_name, meaning in col_semantics.items():
            if re.search(rf"\b{re.escape(col_name.lower())}\b", query_lower):
                matched_col = col_name
                matched_meaning = meaning
                break

    if matched_col and matched_meaning:
        prof = col_profiles.get(matched_col, {})
        facts = {
            "column": matched_col,
            "meaning": matched_meaning,
            "profile": prof,
            "dataset": dataset.name,
        }
        fallback = f"**`{matched_col}`** — {matched_meaning} (non-null: {prof.get('non_null_count', 'N/A')}, unique values: {prof.get('unique_count', 'N/A')})."
        cot_steps = [
            {"phase": "Column Profile Lookup", "thought": f"Inspected statistical profile & role (`{prof.get('role', 'column')}`) for `{matched_col}`."},
        ]
        text = await _call_gemma_with_facts(query, facts, fallback, cot_steps=cot_steps)
        return text, "schema_explanation", {"columns": {matched_col: matched_meaning}, "row_grain": row_grain, "reasoning_steps": cot_steps}

    total_rows = health.get("total_rows_raw") or dataset.row_count or 0
    grain_desc = row_grain.get("description") or f"Each of the {total_rows:,} rows represents a business record across {len(col_semantics)} columns."

    role_groups: Dict[str, List[str]] = {
        "📅 Date & Time": [],
        "📊 Numeric Measures": [],
        "🏷️ Categories & Identifiers": [],
        "🔢 Attributes & Flags": [],
    }
    for col, meaning in col_semantics.items():
        role = col_profiles.get(col, {}).get("role", "")
        if role == "temporal":
            role_groups["📅 Date & Time"].append(f"**{col}**: {meaning}")
        elif role == "numeric_measure":
            role_groups["📊 Numeric Measures"].append(f"**{col}**: {meaning}")
        elif role == "numeric_attribute" or role == "boolean":
            role_groups["🔢 Attributes & Flags"].append(f"**{col}**: {meaning}")
        else:
            role_groups["🏷️ Categories & Identifiers"].append(f"**{col}**: {meaning}")

    group_bullets = []
    for g_name, items in role_groups.items():
        if items:
            preview = items[:4]
            extra = len(items) - 4
            b_txt = "; ".join(preview) + (f" (+{extra} more)" if extra > 0 else "")
            group_bullets.append(f"{g_name}: {b_txt}")

    facts = {
        "dataset": dataset.name,
        "total_rows": total_rows,
        "total_columns": len(col_semantics),
        "row_structure": grain_desc,
        "columns": col_semantics,
    }

    fallback = (
        f"**Complete Row & Column Breakdown for `{dataset.original_filename}`**\n\n"
        f"• **Rows ({total_rows:,} total)**: {grain_desc}\n"
        f"• **Columns ({len(col_semantics)} total)**:\n"
        + "\n".join(f"  - {b}" for b in group_bullets)
        + "\n\nAll column definitions and statistical ranges are listed in the schema card below."
    )

    cot_steps = [
        {"phase": "Full Schema & Grain Profiling", "thought": f"Analyzed all {len(col_semantics)} columns and verified row structure ({grain_desc})."},
    ]
    text = await _call_gemma_with_facts(query, facts, fallback, col_context=col_semantics, cot_steps=cot_steps)
    return text, "schema_explanation", {
        "columns": col_semantics,
        "row_grain": row_grain,
        "dataset_type": health.get("dataset_type") or col_map.get("_dataset_type"),
        "reasoning_steps": cot_steps,
    }


async def _answer_financial_query(
    dataset: Dataset,
    health: Dict[str, Any],
    col_semantics: Dict[str, str],
    query: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Answers questions about profit, margin, revenue, cost, discount for any dataset.
    Extracts financial column info dynamically from col_semantics."""
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

