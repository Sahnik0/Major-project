from datetime import date, timedelta

import polars as pl
import pytest

from apps.api.app.services.forecasting.baselines import (
    AutoETSForecaster,
    CrostonForecaster,
    HistoricAverageForecaster,
    NaiveForecaster,
    SeasonalNaiveForecaster,
    TSBForecaster,
)


@pytest.fixture
def sample_smooth_df():
    start = date(2023, 1, 1)
    dates = [start + timedelta(days=i) for i in range(70)]
    # Seasonal pattern: 10 + day % 7
    qtys = [float(10 + (i % 7)) for i in range(70)]
    return pl.DataFrame({"date": dates, "quantity": qtys})


@pytest.fixture
def sample_intermittent_df():
    start = date(2023, 1, 1)
    dates = [start + timedelta(days=i) for i in range(80)]
    # 0s with intermittent non-zero sales
    qtys = [float(15 if i % 4 == 0 else 0) for i in range(80)]
    return pl.DataFrame({"date": dates, "quantity": qtys})


def test_naive_forecaster(sample_smooth_df):
    forecaster = NaiveForecaster()
    forecaster.fit(sample_smooth_df)
    res = forecaster.predict(horizon=14, quantiles=[0.1, 0.5, 0.9])

    assert len(res.dates) == 14
    assert len(res.point_forecast) == 14
    assert 0.1 in res.quantiles
    assert 0.5 in res.quantiles
    assert 0.9 in res.quantiles
    assert all(q >= 0 for q in res.point_forecast)


def test_seasonal_naive_forecaster(sample_smooth_df):
    forecaster = SeasonalNaiveForecaster(season_length=7)
    forecaster.fit(sample_smooth_df)
    res = forecaster.predict(horizon=14, quantiles=[0.1, 0.5, 0.9])

    assert len(res.dates) == 14
    assert len(res.point_forecast) == 14
    # Should repeat the last 7 days pattern
    expected_cycle = sample_smooth_df["quantity"][-7:].to_list()
    assert res.point_forecast[:7] == expected_cycle
    assert res.point_forecast[7:14] == expected_cycle


def test_auto_ets_forecaster(sample_smooth_df):
    forecaster = AutoETSForecaster(season_length=7)
    forecaster.fit(sample_smooth_df)
    res = forecaster.predict(horizon=14, quantiles=[0.1, 0.5, 0.9])

    assert len(res.point_forecast) == 14
    assert len(res.quantiles[0.1]) == 14
    assert len(res.quantiles[0.9]) == 14
    assert all(q >= 0 for q in res.point_forecast)


def test_croston_forecaster(sample_intermittent_df):
    forecaster = CrostonForecaster(variant="classic")
    forecaster.fit(sample_intermittent_df)
    res = forecaster.predict(horizon=14, quantiles=[0.1, 0.5, 0.9])

    assert len(res.point_forecast) == 14
    assert all(q >= 0 for q in res.point_forecast)
    assert 0.1 in res.quantiles and 0.9 in res.quantiles


def test_tsb_forecaster(sample_intermittent_df):
    forecaster = TSBForecaster()
    forecaster.fit(sample_intermittent_df)
    res = forecaster.predict(horizon=14, quantiles=[0.1, 0.5, 0.9])

    assert len(res.point_forecast) == 14
    assert all(q >= 0 for q in res.point_forecast)


def test_historic_average_forecaster(sample_smooth_df):
    forecaster = HistoricAverageForecaster()
    forecaster.fit(sample_smooth_df)
    res = forecaster.predict(horizon=14, quantiles=[0.1, 0.5, 0.9])

    assert len(res.point_forecast) == 14
    assert res.confidence_label == "low_confidence"
