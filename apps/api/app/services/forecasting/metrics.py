from typing import Sequence
import numpy as np


def wape(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """
    Weighted Absolute Percentage Error: sum(|y - y_hat|) / sum(y)
    Returns float in [0.0, inf).
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    if len(yt) == 0:
        return 0.0

    denom = np.sum(yt)
    numer = np.sum(np.abs(yt - yp))

    if denom <= 1e-9:
        return 0.0 if numer <= 1e-9 else 1.0

    return float(numer / denom)


def mase(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    y_train: Sequence[float],
    seasonality: int = 1,
) -> float:
    """
    Mean Absolute Scaled Error:
    Mean(|y_test - y_pred|) / Mean(|y_train[t] - y_train[t - seasonality]|)
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    ytr = np.asarray(y_train, dtype=np.float64)

    if len(yt) == 0:
        return 0.0

    test_mae = np.mean(np.abs(yt - yp))

    if len(ytr) <= seasonality:
        seasonality = 1

    if len(ytr) <= 1:
        return float(test_mae)

    naive_diffs = np.abs(ytr[seasonality:] - ytr[:-seasonality])
    train_scale = np.mean(naive_diffs)

    if train_scale <= 1e-9:
        return 0.0 if test_mae <= 1e-9 else float(test_mae)

    return float(test_mae / train_scale)


def pinball_loss(
    y_true: Sequence[float],
    y_quantile: Sequence[float],
    tau: float,
) -> float:
    """
    Pinball loss (Quantile loss) for quantile level tau in (0, 1):
    L_tau(y, q) = mean(max(tau * (y - q), (1 - tau) * (q - y)))
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yq = np.asarray(y_quantile, dtype=np.float64)

    if len(yt) == 0:
        return 0.0

    error = yt - yq
    loss = np.maximum(tau * error, (tau - 1.0) * error)
    return float(np.mean(loss))


def interval_coverage(
    y_true: Sequence[float],
    lower_band: Sequence[float],
    upper_band: Sequence[float],
) -> float:
    """
    Empirical interval coverage: proportion of actual values within [lower, upper].
    """
    yt = np.asarray(y_true, dtype=np.float64)
    lb = np.asarray(lower_band, dtype=np.float64)
    ub = np.asarray(upper_band, dtype=np.float64)

    if len(yt) == 0:
        return 0.0

    covered = (yt >= lb - 1e-6) & (yt <= ub + 1e-6)
    return float(np.mean(covered))

