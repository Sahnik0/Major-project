from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from apps.api.app.services.forecasting.base import ForecastPrediction
from apps.api.app.services.forecasting.metrics import (
    interval_coverage,
    mase,
    pinball_loss,
    wape,
)
from apps.api.app.services.forecasting.router import get_candidate_models_for_class


@dataclass
class ModelEvaluationMetric:
    model_name: str
    wape: float
    mase: float
    pinball_loss_10: float
    pinball_loss_50: float
    pinball_loss_90: float
    interval_coverage_80: float
    folds_evaluated: int


@dataclass
class ProductBacktestResult:
    product: str
    series_class: str
    best_model_name: str
    metrics_table: List[Dict[str, Any]]
    final_forecast: ForecastPrediction


def run_rolling_origin_backtest_for_product(
    df_product: pl.DataFrame,
    product_name: str,
    series_class: str,
    horizon: int = 28,
    num_folds: int = 3,
    season_length: int = 7,
) -> Optional[ProductBacktestResult]:
    """
    Executes rolling-origin backtest for a single product across suitable candidate models.
    """
    if series_class == "inactive":
        return None

    df_sorted = df_product.sort("date")
    total_len = len(df_sorted)

    # Determine feasible horizon and folds
    effective_horizon = min(horizon, max(7, total_len // 4)) if total_len < horizon * 2 else horizon
    min_train = max(7, season_length * 2) if total_len >= season_length * 2 else 5

    # Compute feasible number of folds
    feasible_folds = min(num_folds, max(1, (total_len - min_train) // effective_horizon))

    candidates = get_candidate_models_for_class(
        series_class=series_class,
        history_length=total_len,
        season_length=season_length,
    )

    if not candidates:
        return None

    # Track metrics per model: list of fold metrics
    model_fold_results: Dict[str, List[Dict[str, float]]] = {m.name: [] for m in candidates}

    # Evaluate each fold
    for fold_idx in range(feasible_folds):
        # Rolling cutoffs: e.g. for fold_idx = 0 (most recent fold)
        test_end = total_len - fold_idx * effective_horizon
        test_start = test_end - effective_horizon
        if test_start < min_train:
            break

        train_df = df_sorted[:test_start]
        test_df = df_sorted[test_start:test_end]

        y_train = train_df["quantity"].to_numpy()
        y_test = test_df["quantity"].to_numpy()

        for model in candidates:
            try:
                model.fit(train_df, target_col="quantity", date_col="date")
                pred = model.predict(horizon=len(y_test), quantiles=[0.1, 0.5, 0.9])

                pred_pt = np.asarray(pred.point_forecast)
                q10 = np.asarray(pred.quantiles.get(0.1, pred_pt * 0.8))
                q50 = np.asarray(pred.quantiles.get(0.5, pred_pt))
                q90 = np.asarray(pred.quantiles.get(0.9, pred_pt * 1.2))

                m_wape = wape(y_test, pred_pt)
                m_mase = mase(y_test, pred_pt, y_train, seasonality=season_length)
                m_pb10 = pinball_loss(y_test, q10, 0.1)
                m_pb50 = pinball_loss(y_test, q50, 0.5)
                m_pb90 = pinball_loss(y_test, q90, 0.9)
                m_cov = interval_coverage(y_test, q10, q90)

                model_fold_results[model.name].append(
                    {
                        "wape": m_wape,
                        "mase": m_mase,
                        "pinball_loss_10": m_pb10,
                        "pinball_loss_50": m_pb50,
                        "pinball_loss_90": m_pb90,
                        "interval_coverage_80": m_cov,
                    }
                )
            except Exception:
                # If model failed on fold, skip
                continue

    # Aggregate metric averages
    metrics_summary: List[ModelEvaluationMetric] = []
    for model_name, folds in model_fold_results.items():
        if not folds:
            continue
        n_f = len(folds)
        metrics_summary.append(
            ModelEvaluationMetric(
                model_name=model_name,
                wape=round(float(np.mean([f["wape"] for f in folds])), 4),
                mase=round(float(np.mean([f["mase"] for f in folds])), 4),
                pinball_loss_10=round(float(np.mean([f["pinball_loss_10"] for f in folds])), 4),
                pinball_loss_50=round(float(np.mean([f["pinball_loss_50"] for f in folds])), 4),
                pinball_loss_90=round(float(np.mean([f["pinball_loss_90"] for f in folds])), 4),
                interval_coverage_80=round(
                    float(np.mean([f["interval_coverage_80"] for f in folds])), 4
                ),
                folds_evaluated=n_f,
            )
        )

    if not metrics_summary:
        # Fallback to naive if all failed
        default_model = candidates[0]
        default_model.fit(df_sorted, target_col="quantity", date_col="date")
        final_pred = default_model.predict(horizon=horizon)
        return ProductBacktestResult(
            product=product_name,
            series_class=series_class,
            best_model_name=default_model.name,
            metrics_table=[],
            final_forecast=final_pred,
        )

    # Best model selection rule: Lowest WAPE, tiebreaker MASE
    metrics_summary.sort(key=lambda m: (m.wape, m.mase))
    best_eval = metrics_summary[0]
    best_model_name = best_eval.model_name

    # Find winning forecaster instance and fit on full dataset
    winning_model = next((m for m in candidates if m.name == best_model_name), candidates[0])
    winning_model.fit(df_sorted, target_col="quantity", date_col="date")
    final_pred = winning_model.predict(horizon=horizon, quantiles=[0.1, 0.5, 0.9])

    return ProductBacktestResult(
        product=product_name,
        series_class=series_class,
        best_model_name=best_model_name,
        metrics_table=[asdict(m) for m in metrics_summary],
        final_forecast=final_pred,
    )
