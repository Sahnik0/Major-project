from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import polars as pl
from statsforecast import StatsForecast
from statsforecast.models import (
    TSB,
    AutoARIMA,
    AutoETS,
    CrostonClassic,
    CrostonSBA,
)

from apps.api.app.services.forecasting.base import Forecaster, ForecastPrediction


def _generate_future_dates(last_date: date, horizon: int, freq: str = "D") -> List[str]:
    """Generate ISO date strings for the forecast horizon."""
    if freq.upper() in ("W", "1W"):
        return [(last_date + timedelta(weeks=i + 1)).isoformat() for i in range(horizon)]
    return [(last_date + timedelta(days=i + 1)).isoformat() for i in range(horizon)]


def compute_residual_quantiles(
    y_train: np.ndarray,
    in_sample_preds: np.ndarray,
    point_forecast: np.ndarray,
    quantiles: List[float],
) -> Dict[float, List[float]]:
    """
    Derives calibrated forecast quantiles using conformal residual distribution.
    Residuals: e_t = y_t - y_hat_t.
    Forecast quantile: max(0.0, y_hat + Quantile(e_t)).
    """
    resids = y_train - in_sample_preds
    res_dict: Dict[float, List[float]] = {}

    for q in quantiles:
        if len(resids) >= 3:
            shift = float(np.percentile(resids, q * 100))
        else:
            # Fallback spread proportional to mean
            mean_val = float(np.mean(y_train)) if len(y_train) > 0 else 1.0
            shift = (q - 0.5) * mean_val * 0.5

        q_vals = np.maximum(0.0, point_forecast + shift)
        res_dict[q] = [round(float(v), 2) for v in q_vals]

    return res_dict


class NaiveForecaster(Forecaster):
    name: str = "Naive"

    def __init__(self):
        self.last_value = 0.0
        self.y_train: np.ndarray = np.array([])
        self.last_date: Optional[date] = None

    def fit(
        self, df: pl.DataFrame, target_col: str = "quantity", date_col: str = "date"
    ) -> "NaiveForecaster":
        self.y_train = df[target_col].to_numpy()
        self.last_value = float(self.y_train[-1]) if len(self.y_train) > 0 else 0.0
        dates = df[date_col].to_list()
        self.last_date = dates[-1] if dates else date.today()
        return self

    def predict(self, horizon: int, quantiles: Optional[List[float]] = None) -> ForecastPrediction:
        if quantiles is None:
            quantiles = [0.1, 0.5, 0.9]

        point = np.full(horizon, max(0.0, self.last_value), dtype=np.float64)
        in_sample = np.roll(self.y_train, 1)
        in_sample[0] = self.y_train[0] if len(self.y_train) > 0 else 0.0

        q_dict = compute_residual_quantiles(self.y_train, in_sample, point, quantiles)
        future_dates = _generate_future_dates(self.last_date, horizon)

        return ForecastPrediction(
            dates=future_dates,
            point_forecast=[round(float(v), 2) for v in point],
            quantiles=q_dict,
            model_name=self.name,
        )


class SeasonalNaiveForecaster(Forecaster):
    name: str = "SeasonalNaive"

    def __init__(self, season_length: int = 7):
        self.season_length = season_length
        self.y_train: np.ndarray = np.array([])
        self.last_date: Optional[date] = None

    def fit(
        self, df: pl.DataFrame, target_col: str = "quantity", date_col: str = "date"
    ) -> "SeasonalNaiveForecaster":
        self.y_train = df[target_col].to_numpy()
        dates = df[date_col].to_list()
        self.last_date = dates[-1] if dates else date.today()
        return self

    def predict(self, horizon: int, quantiles: Optional[List[float]] = None) -> ForecastPrediction:
        if quantiles is None:
            quantiles = [0.1, 0.5, 0.9]

        m = self.season_length
        n = len(self.y_train)
        if n < m:
            # Fall back to naive if series is shorter than one season
            fallback = NaiveForecaster()
            fallback.y_train = self.y_train
            fallback.last_value = self.y_train[-1] if n > 0 else 0.0
            fallback.last_date = self.last_date
            res = fallback.predict(horizon, quantiles)
            res.model_name = self.name
            return res

        recent_season = self.y_train[-m:]
        repeats = int(np.ceil(horizon / m))
        point = np.tile(recent_season, repeats)[:horizon]
        point = np.maximum(0.0, point)

        in_sample = np.roll(self.y_train, m)
        in_sample[:m] = self.y_train[:m]

        q_dict = compute_residual_quantiles(self.y_train, in_sample, point, quantiles)
        future_dates = _generate_future_dates(self.last_date, horizon)

        return ForecastPrediction(
            dates=future_dates,
            point_forecast=[round(float(v), 2) for v in point],
            quantiles=q_dict,
            model_name=self.name,
        )


class HistoricAverageForecaster(Forecaster):
    name: str = "HistoricAverage"

    def __init__(self):
        self.mean_val = 0.0
        self.y_train: np.ndarray = np.array([])
        self.last_date: Optional[date] = None

    def fit(
        self, df: pl.DataFrame, target_col: str = "quantity", date_col: str = "date"
    ) -> "HistoricAverageForecaster":
        self.y_train = df[target_col].to_numpy()
        self.mean_val = float(np.mean(self.y_train)) if len(self.y_train) > 0 else 0.0
        dates = df[date_col].to_list()
        self.last_date = dates[-1] if dates else date.today()
        return self

    def predict(self, horizon: int, quantiles: Optional[List[float]] = None) -> ForecastPrediction:
        if quantiles is None:
            quantiles = [0.1, 0.5, 0.9]

        point = np.full(horizon, max(0.0, self.mean_val), dtype=np.float64)
        in_sample = np.full_like(self.y_train, self.mean_val)
        q_dict = compute_residual_quantiles(self.y_train, in_sample, point, quantiles)
        future_dates = _generate_future_dates(self.last_date, horizon)

        return ForecastPrediction(
            dates=future_dates,
            point_forecast=[round(float(v), 2) for v in point],
            quantiles=q_dict,
            model_name=self.name,
            confidence_label="low_confidence",
        )


class StatsForecastBaseWrapper(Forecaster):
    """Generic wrapper for statsforecast models ensuring quantile compliance."""

    def __init__(self, model_obj, name: str, season_length: int = 7):
        self.model_obj = model_obj
        self.name = name
        self.season_length = season_length
        self.df_pandas: Optional[pd.DataFrame] = None
        self.last_date: Optional[date] = None
        self.y_train: np.ndarray = np.array([])

    def fit(
        self, df: pl.DataFrame, target_col: str = "quantity", date_col: str = "date"
    ) -> "StatsForecastBaseWrapper":
        self.y_train = df[target_col].to_numpy()
        dates = df[date_col].to_list()
        self.last_date = dates[-1] if dates else date.today()

        self.df_pandas = pd.DataFrame(
            {
                "unique_id": ["series_1"] * len(df),
                "ds": pd.to_datetime(df[date_col].to_list()),
                "y": df[target_col].to_numpy().astype(np.float64),
            }
        )
        return self

    def predict(self, horizon: int, quantiles: Optional[List[float]] = None) -> ForecastPrediction:
        if quantiles is None:
            quantiles = [0.1, 0.5, 0.9]

        try:
            sf = StatsForecast(models=[self.model_obj], freq="D", n_jobs=1)
            sf.fit(self.df_pandas)

            # Request 80% prediction interval for 10% and 90% quantiles
            pred_df = sf.predict(h=horizon, level=[80])
            model_col = self.model_obj.__class__.__name__

            if model_col in pred_df.columns:
                point = np.maximum(0.0, pred_df[model_col].to_numpy())
            else:
                # First non-id, non-ds col
                val_cols = [c for c in pred_df.columns if c not in ("unique_id", "ds")]
                point = np.maximum(0.0, pred_df[val_cols[0]].to_numpy())

            q_dict: Dict[float, List[float]] = {}
            lo_col = f"{model_col}-lo-80"
            hi_col = f"{model_col}-hi-80"

            if lo_col in pred_df.columns and hi_col in pred_df.columns:
                for q in quantiles:
                    if abs(q - 0.1) < 1e-4:
                        q_dict[q] = [
                            round(float(v), 2) for v in np.maximum(0.0, pred_df[lo_col].to_numpy())
                        ]
                    elif abs(q - 0.9) < 1e-4:
                        q_dict[q] = [
                            round(float(v), 2) for v in np.maximum(0.0, pred_df[hi_col].to_numpy())
                        ]
                    elif abs(q - 0.5) < 1e-4:
                        q_dict[q] = [round(float(v), 2) for v in point]
                    else:
                        # Interpolate
                        weight = (q - 0.1) / (0.9 - 0.1)
                        interp = pred_df[lo_col].to_numpy() + weight * (
                            pred_df[hi_col].to_numpy() - pred_df[lo_col].to_numpy()
                        )
                        q_dict[q] = [round(float(v), 2) for v in np.maximum(0.0, interp)]
            else:
                # Empirical residual fallback
                in_sample = np.full_like(self.y_train, float(np.mean(point)))
                q_dict = compute_residual_quantiles(self.y_train, in_sample, point, quantiles)

        except Exception:
            # Robust fallback to seasonal naive or naive on fit error
            fallback = SeasonalNaiveForecaster(season_length=self.season_length)
            fallback.y_train = self.y_train
            fallback.last_date = self.last_date
            res = fallback.predict(horizon, quantiles)
            res.model_name = self.name
            return res

        future_dates = _generate_future_dates(self.last_date, horizon)
        return ForecastPrediction(
            dates=future_dates,
            point_forecast=[round(float(v), 2) for v in point],
            quantiles=q_dict,
            model_name=self.name,
        )


class AutoETSForecaster(StatsForecastBaseWrapper):
    def __init__(self, season_length: int = 7):
        super().__init__(
            AutoETS(season_length=season_length), name="AutoETS", season_length=season_length
        )


class AutoARIMAForecaster(StatsForecastBaseWrapper):
    def __init__(self, season_length: int = 7):
        super().__init__(
            AutoARIMA(season_length=season_length), name="AutoARIMA", season_length=season_length
        )


class CrostonForecaster(StatsForecastBaseWrapper):
    def __init__(self, variant: str = "classic"):
        model = CrostonSBA() if variant.lower() == "sba" else CrostonClassic()
        name = "CrostonSBA" if variant.lower() == "sba" else "CrostonClassic"
        super().__init__(model, name=name, season_length=1)


class TSBForecaster(StatsForecastBaseWrapper):
    def __init__(self, alpha_d: float = 0.2, alpha_p: float = 0.2):
        super().__init__(TSB(alpha_d=alpha_d, alpha_p=alpha_p), name="TSB", season_length=1)
