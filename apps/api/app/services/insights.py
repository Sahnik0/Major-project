from typing import Any, Dict, List
import numpy as np
from sqlalchemy.orm import Session

from apps.api.app.db import Dataset, ProductForecast


def build_business_facts(dataset_id: str, db: Session) -> Dict[str, Any]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return {}

    forecasts = db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset_id).all()
    if not forecasts:
        return {}

    total_forecast_units = 0.0
    products_count = len(forecasts)
    high_risk_products: List[Dict[str, Any]] = []
    reorder_actions: List[Dict[str, Any]] = []
    model_wins: Dict[str, int] = {}
    wapes: List[float] = []

    for pf in forecasts:
        pts = pf.point_forecast
        total_p = sum(pts) if pts else 0.0
        total_forecast_units += total_p

        # Model counts
        model_wins[pf.chosen_model] = model_wins.get(pf.chosen_model, 0) + 1

        # WAPE from backtest table
        if pf.backtest_table:
            for m in pf.backtest_table:
                if m.get("model_name") == pf.chosen_model and m.get("wape") is not None:
                    wapes.append(float(m["wape"]))

        inv = pf.inventory_advice or {}
        risk = inv.get("stockout_risk", "unknown")
        reorder_qty = inv.get("recommended_order_qty")

        if risk == "high":
            high_risk_products.append({
                "product": pf.product_id,
                "reorder_point": inv.get("reorder_point", 0),
                "days_of_cover": inv.get("days_of_cover"),
            })

        if reorder_qty and reorder_qty > 0:
            reorder_actions.append({
                "product": pf.product_id,
                "recommended_qty": int(reorder_qty),
                "reorder_point": int(inv.get("reorder_point", 0)),
            })

    # Sort reorder actions by highest recommended qty
    reorder_actions.sort(key=lambda x: x["recommended_qty"], reverse=True)

    avg_wape_pct = round(float(np.mean(wapes)) * 100, 1) if wapes else None

    # Health report caveats
    health = dataset.health_report or {}
    class_dist = health.get("class_distribution", {})

    facts = {
        "dataset_name": dataset.name,
        "total_products": products_count,
        "total_forecast_units": int(round(total_forecast_units)),
        "high_risk_count": len(high_risk_products),
        "reorder_actions_count": len(reorder_actions),
        "top_reorders": reorder_actions[:5],
        "average_forecast_error_pct": avg_wape_pct,
        "inactive_products_count": class_dist.get("inactive", 0),
        "short_history_products_count": class_dist.get("short_history", 0),
        "most_frequent_model": max(model_wins, key=model_wins.get) if model_wins else "None",
    }
    return facts

