from apps.api.app.services.inventory import compute_inventory_advice


def test_inventory_math_hand_calculated():
    # 10 units per day for 14 days
    pts = [10.0] * 14
    # Upper quantile: 12 units per day
    q_upper = [12.0] * 14

    # Lead time = 5, review period = 5 -> L+R = 10
    # LTD = 50.0, Review period demand = 100.0
    # Safety stock from quantile: 5 * 12 - 50 = 10.0
    # ROP = 50 + 10 = 60.0
    # Target level S = 100 + 10 = 110.0
    # Current stock = 40.0 (<= 60.0 ROP -> Trigger Reorder)
    # Recommended Q = 110 - 40 = 70
    res = compute_inventory_advice(
        product="TEST_SKU",
        point_forecast=pts,
        quantile_upper_forecast=q_upper,
        lead_time_days=5,
        review_period_days=5,
        service_level=0.90,
        current_stock=40.0,
    )

    assert res.average_daily_demand == 10.0
    assert res.lead_time_demand == 50.0
    assert res.review_period_demand == 100.0
    assert res.safety_stock == 10.0
    assert res.reorder_point == 60.0
    assert res.target_order_level == 110.0
    assert res.recommended_order_qty == 70
    assert res.days_of_cover == 4.0
    assert res.stockout_risk == "high"  # days of cover (4) < lead time (5)
    assert not res.overstock_flag


def test_moq_and_pack_size_rounding():
    # Target order level = 110, ROP = 60, current stock = 40 -> raw Q = 70
    pts = [10.0] * 14
    q_upper = [12.0] * 14

    # Case A: MOQ = 100 -> Q = 100
    res_moq = compute_inventory_advice(
        product="TEST_SKU",
        point_forecast=pts,
        quantile_upper_forecast=q_upper,
        lead_time_days=5,
        review_period_days=5,
        current_stock=40.0,
        min_order_qty=100,
    )
    assert res_moq.recommended_order_qty == 100

    # Case B: MOQ = 100, Pack size = 12 -> ceil(100 / 12) * 12 = 9 * 12 = 108
    res_pack = compute_inventory_advice(
        product="TEST_SKU",
        point_forecast=pts,
        quantile_upper_forecast=q_upper,
        lead_time_days=5,
        review_period_days=5,
        current_stock=40.0,
        min_order_qty=100,
        pack_size=12,
    )
    assert res_pack.recommended_order_qty == 108


def test_inventory_no_order_needed():
    # Current stock 80 > ROP 60 -> Recommended qty = 0, risk = low
    pts = [10.0] * 14
    q_upper = [12.0] * 14

    res = compute_inventory_advice(
        product="TEST_SKU",
        point_forecast=pts,
        quantile_upper_forecast=q_upper,
        lead_time_days=5,
        review_period_days=5,
        current_stock=80.0,
    )
    assert res.recommended_order_qty == 0
    assert res.stockout_risk == "low"
    assert res.days_of_cover == 8.0


def test_inventory_missing_stock_handling():
    # Current stock is None
    pts = [10.0] * 14
    q_upper = [12.0] * 14

    res = compute_inventory_advice(
        product="TEST_SKU",
        point_forecast=pts,
        quantile_upper_forecast=q_upper,
        lead_time_days=5,
        review_period_days=5,
        current_stock=None,
    )
    assert res.current_stock is None
    assert res.stockout_risk == "unknown"
    assert res.recommended_order_qty is None
    assert "below 60 units" in res.advice_text
