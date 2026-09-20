import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy import stats


@dataclass
class InventoryAdviceResult:
    product: str
    lead_time_days: int
    review_period_days: int
    service_level: float
    current_stock: Optional[float]
    average_daily_demand: float
    lead_time_demand: float
    review_period_demand: float
    safety_stock: float
    reorder_point: float
    target_order_level: float
    recommended_order_qty: Optional[int]
    days_of_cover: Optional[float]
    stockout_risk: str  # "low", "medium", "high", "unknown"
    overstock_flag: bool
    advice_text: str


def compute_inventory_advice(
    product: str,
    point_forecast: Sequence[float],
    quantile_upper_forecast: Optional[Sequence[float]] = None,
    historical_daily_std: float = 0.0,
    lead_time_days: int = 7,
    review_period_days: int = 7,
    service_level: float = 0.90,
    current_stock: Optional[float] = None,
    min_order_qty: Optional[int] = None,
    pack_size: Optional[int] = None,
) -> InventoryAdviceResult:
    """
    Pure Python deterministic inventory calculations.
    """
    pts = [float(v) for v in point_forecast]
    horizon = len(pts)
    avg_daily = float(np.mean(pts)) if horizon > 0 else 0.0

    # Ensure we can cover lead_time + review_period
    total_planning_days = lead_time_days + review_period_days

    # Cumulative demand calculations
    if horizon >= lead_time_days:
        lead_time_demand = float(np.sum(pts[:lead_time_days]))
    else:
        lead_time_demand = float(np.sum(pts) + avg_daily * (lead_time_days - horizon))

    if horizon >= total_planning_days:
        review_period_demand = float(np.sum(pts[:total_planning_days]))
    else:
        review_period_demand = float(np.sum(pts) + avg_daily * (total_planning_days - horizon))

    # Safety Stock computation:
    # Option A: Upper quantile forecast spread over lead time
    # Option B: Analytic normal approximation z * sigma_L
    z = stats.norm.ppf(service_level)

    if quantile_upper_forecast is not None and len(quantile_upper_forecast) >= lead_time_days:
        upper_pts = [float(v) for v in quantile_upper_forecast[:lead_time_days]]
        quantile_ltd = float(np.sum(upper_pts))
        safety_stock = max(0.0, quantile_ltd - lead_time_demand)
    else:
        sigma_l = math.sqrt(lead_time_days) * max(historical_daily_std, 0.2 * max(avg_daily, 1.0))
        safety_stock = float(z * sigma_l)

    safety_stock = round(max(0.0, safety_stock), 2)
    reorder_point = round(lead_time_demand + safety_stock, 2)
    target_order_level = round(review_period_demand + safety_stock, 2)

    # Order Quantity calculation
    recommended_qty: Optional[int] = None
    days_of_cover: Optional[float] = None
    stockout_risk: str = "unknown"
    overstock_flag: bool = False
    advice_text: str = ""

    if current_stock is not None:
        # Days of cover
        if avg_daily > 1e-4:
            days_of_cover = round(float(current_stock / avg_daily), 1)
        else:
            days_of_cover = 999.0 if current_stock > 0 else 0.0

        # Stockout risk
        if current_stock < 0.5 * reorder_point or days_of_cover < lead_time_days:
            stockout_risk = "high"
        elif current_stock < reorder_point:
            stockout_risk = "medium"
        else:
            stockout_risk = "low"

        # Overstock flag: >3x protection period & >2x ROP
        if days_of_cover > 3 * total_planning_days and current_stock > 2 * reorder_point:
            overstock_flag = True

        # Reorder trigger
        if current_stock <= reorder_point:
            raw_needed = target_order_level - current_stock
            qty = max(0.0, raw_needed)

            # MOQ
            if min_order_qty and min_order_qty > 0:
                qty = max(qty, float(min_order_qty))

            # Pack size rounding up
            if pack_size and pack_size > 1:
                qty = math.ceil(qty / pack_size) * pack_size
            else:
                qty = math.ceil(qty)

            recommended_qty = int(qty)
            advice_text = (
                f"Reorder {recommended_qty} units now (current stock of {int(current_stock)} "
                f"is below reorder point of {int(math.ceil(reorder_point))})."
            )
        else:
            recommended_qty = 0
            advice_text = (
                f"Stock is healthy ({days_of_cover:.1f} days of cover). No order needed currently."
            )
    else:
        # Missing current stock
        base_order = max(1, int(math.ceil(target_order_level - reorder_point)))
        if min_order_qty and min_order_qty > base_order:
            base_order = min_order_qty
        if pack_size and pack_size > 1:
            base_order = math.ceil(base_order / pack_size) * pack_size

        stockout_risk = "unknown"
        advice_text = (
            f"Order {base_order} units whenever stock falls below "
            f"{int(math.ceil(reorder_point))} units."
        )

    return InventoryAdviceResult(
        product=product,
        lead_time_days=lead_time_days,
        review_period_days=review_period_days,
        service_level=service_level,
        current_stock=current_stock,
        average_daily_demand=round(avg_daily, 2),
        lead_time_demand=round(lead_time_demand, 2),
        review_period_demand=round(review_period_demand, 2),
        safety_stock=safety_stock,
        reorder_point=reorder_point,
        target_order_level=target_order_level,
        recommended_order_qty=recommended_qty,
        days_of_cover=days_of_cover,
        stockout_risk=stockout_risk,
        overstock_flag=overstock_flag,
        advice_text=advice_text,
    )
