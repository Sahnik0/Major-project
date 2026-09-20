from datetime import date, timedelta

import polars as pl

from apps.api.app.services.forecasting.backtest import run_rolling_origin_backtest_for_product


def test_rolling_origin_backtest_smooth():
    start = date(2023, 1, 1)
    dates = [start + timedelta(days=i) for i in range(120)]
    # Clear weekly pattern
    qtys = [float(20 + 5 * (i % 7)) for i in range(120)]
    df = pl.DataFrame({"date": dates, "quantity": qtys})

    res = run_rolling_origin_backtest_for_product(
        df_product=df,
        product_name="PROD_SMOOTH_01",
        series_class="smooth",
        horizon=14,
        num_folds=2,
    )

    assert res is not None
    assert res.product == "PROD_SMOOTH_01"
    assert res.series_class == "smooth"
    assert len(res.metrics_table) > 0
    # Winner should be evaluated
    model_names = [m["model_name"] for m in res.metrics_table]
    assert res.best_model_name in model_names
    assert len(res.final_forecast.point_forecast) == 14
    # All metrics are positive numbers
    for m in res.metrics_table:
        assert m["wape"] >= 0.0
        assert m["mase"] >= 0.0
        assert 0.0 <= m["interval_coverage_80"] <= 1.0


def test_rolling_origin_backtest_inactive():
    df = pl.DataFrame({"date": [date(2023, 1, 1)], "quantity": [10.0]})
    res = run_rolling_origin_backtest_for_product(
        df_product=df,
        product_name="PROD_INACTIVE_01",
        series_class="inactive",
    )
    assert res is None
