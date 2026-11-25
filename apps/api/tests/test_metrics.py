import pytest
from apps.api.app.services.forecasting.metrics import (
    interval_coverage,
    mase,
    pinball_loss,
    wape,
)


def test_wape_hand_calculated():
    # Actuals: 10, 20, 30 -> Sum = 60
    # Preds:   12, 18, 35 -> Diff: |2| + |2| + |5| = 9
    # WAPE = 9 / 60 = 0.15
    y_true = [10.0, 20.0, 30.0]
    y_pred = [12.0, 18.0, 35.0]
    assert wape(y_true, y_pred) == pytest.approx(0.15, rel=1e-5)


def test_wape_zero_actuals():
    assert wape([0.0, 0.0], [0.0, 0.0]) == 0.0
    assert wape([0.0, 0.0], [5.0, 5.0]) == 1.0


def test_mase_hand_calculated():
    # Train: [10, 12, 14, 16, 18] -> diffs: [2, 2, 2, 2] -> train_scale = 2.0
    # Test actual: [20, 22]
    # Test pred:   [19, 24] -> errors: |1| + |2| = 3 -> MAE = 1.5
    # MASE = 1.5 / 2.0 = 0.75
    y_train = [10.0, 12.0, 14.0, 16.0, 18.0]
    y_test = [20.0, 22.0]
    y_pred = [19.0, 24.0]
    assert mase(y_test, y_pred, y_train, seasonality=1) == pytest.approx(0.75, rel=1e-5)


def test_pinball_loss_hand_calculated():
    # Test single point overprediction: y = 10, q = 12
    # tau = 0.1: (1 - 0.1) * (12 - 10) = 0.9 * 2 = 1.8
    # tau = 0.9: (1 - 0.9) * (12 - 10) = 0.1 * 2 = 0.2
    assert pinball_loss([10.0], [12.0], tau=0.1) == pytest.approx(1.8, rel=1e-5)
    assert pinball_loss([10.0], [12.0], tau=0.9) == pytest.approx(0.2, rel=1e-5)

    # Test single point underprediction: y = 10, q = 8
    # tau = 0.1: 0.1 * (10 - 8) = 0.2
    # tau = 0.9: 0.9 * (10 - 8) = 1.8
    assert pinball_loss([10.0], [8.0], tau=0.1) == pytest.approx(0.2, rel=1e-5)
    assert pinball_loss([10.0], [8.0], tau=0.9) == pytest.approx(1.8, rel=1e-5)


def test_interval_coverage_hand_calculated():
    y_true = [10.0, 20.0, 30.0, 40.0, 50.0]
    lower = [8.0, 15.0, 32.0, 38.0, 45.0]
    upper = [12.0, 25.0, 35.0, 42.0, 55.0]
    # Indices 0 (10 in [8,12]), 1 (20 in [15,25]), 3 (40 in [38,42]), 4 (50 in [45,55]) are covered.
    # Index 2 (30 not in [32,35]) is not covered.
    # 4 out of 5 covered -> 0.80
    assert interval_coverage(y_true, lower, upper) == pytest.approx(0.80, rel=1e-5)

