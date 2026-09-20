from datetime import date

import numpy as np

from apps.api.app.services.profiling import calculate_syntetos_boylan


def test_smooth_classification():
    # Regular daily demand with very low variance
    # ADI = 1.0 (< 1.32), CV^2 ~ 0.01 (< 0.49)
    q = np.array([10.0, 11.0, 10.0, 12.0, 10.0, 11.0, 10.0] * 10)  # 70 periods
    series_class, adi, cv2 = calculate_syntetos_boylan(q, min_periods=60)
    assert series_class == "smooth"
    assert adi < 1.32
    assert cv2 < 0.49


def test_erratic_classification():
    # Demand on almost every period (low ADI), but extreme variance (high CV^2)
    # E.g. 1, 100, 2, 80, 1, 95
    base = [2.0, 100.0, 3.0, 120.0, 2.0, 90.0, 1.0]
    q = np.array(base * 10)  # 70 periods
    series_class, adi, cv2 = calculate_syntetos_boylan(q, min_periods=60)
    assert series_class == "erratic"
    assert adi < 1.32
    assert cv2 >= 0.49


def test_intermittent_classification():
    # Demand occurs once every 4 periods (ADI = 4.0 >= 1.32)
    # But when it occurs, the quantity is virtually constant (CV^2 < 0.49)
    pattern = [10.0, 0.0, 0.0, 0.0] * 20  # 80 periods
    q = np.array(pattern)
    series_class, adi, cv2 = calculate_syntetos_boylan(q, min_periods=60)
    assert series_class == "intermittent"
    assert adi >= 1.32
    assert cv2 < 0.49


def test_lumpy_classification():
    # Demand occurs infrequently (high ADI) AND has high variance (high CV^2)
    pattern = [
        5.0,
        0.0,
        0.0,
        0.0,
        100.0,
        0.0,
        0.0,
        0.0,
        2.0,
        0.0,
        0.0,
        0.0,
        80.0,
        0.0,
        0.0,
        0.0,
    ] * 5
    q = np.array(pattern)  # 80 periods
    series_class, adi, cv2 = calculate_syntetos_boylan(q, min_periods=60)
    assert series_class == "lumpy"
    assert adi >= 1.32
    assert cv2 >= 0.49


def test_short_history_classification():
    # Fewer than 60 daily points
    q = np.array([10.0, 12.0, 11.0] * 10)  # 30 points
    series_class, adi, cv2 = calculate_syntetos_boylan(q, min_periods=60)
    assert series_class == "short_history"


def test_inactive_classification():
    # Series has plenty of history, but last sale occurred > 60 days before dataset end
    q = np.array([10.0] * 70)
    dataset_max_date = date(2023, 12, 31)
    last_sale_date = date(2023, 9, 1)  # ~120 days before
    series_class, adi, cv2 = calculate_syntetos_boylan(
        q,
        min_periods=60,
        dataset_max_date=dataset_max_date,
        last_sale_date=last_sale_date,
        inactive_days=60,
    )
    assert series_class == "inactive"
