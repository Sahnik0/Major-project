import logging
from typing import Any, Dict, List, Optional
import numpy as np
import polars as pl
import torch

from apps.api.app.config import settings
from apps.api.app.services.forecasting.base import Forecaster, ForecastPrediction
from apps.api.app.services.forecasting.baselines import AutoETSForecaster, CrostonForecaster

logger = logging.getLogger("advisor.forecasting.timesfm")

# Global singleton cache for loaded TimesFM model
_model_instance = None
_model_load_attempted = False
_model_load_failed = False


def get_timesfm_model():
    """
    Lazily loads and caches the TimesFM model instance.
    Uses CUDA if available, with fallback to CPU.
    Gracefully handles missing weights, network timeouts, or import errors.
    """
    global _model_instance, _model_load_attempted, _model_load_failed

    if _model_instance is not None:
        return _model_instance

    if _model_load_failed:
        return None

    _model_load_attempted = True

    try:
        import timesfm

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        logger.info(f"Attempting to load TimesFM model (version: {settings.TIMESFM_VERSION}) onto {device}...")

        # Select model class based on settings
        if settings.TIMESFM_VERSION.startswith("3"):
            repo_id = "google/timesfm-3.0-500m-pytorch"
            # Attempt TimesFM3
            try:
                model = timesfm.TimesFM3Forecaster.from_pretrained(repo_id)
            except Exception:
                # Fallback to 2.5 repo
                repo_id = "google/timesfm-2.5-200m-pytorch"
                model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(repo_id)
        else:
            repo_id = "google/timesfm-2.5-200m-pytorch"
            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(repo_id)

        _model_instance = model
        logger.info(f"Successfully loaded TimesFM model from {repo_id}")
        return _model_instance

    except Exception as exc:
        logger.warning(
            f"TimesFM could not be loaded ({exc}). Falling back to statistical baselines."
        )
        _model_load_failed = True
        return None


class TimesFMForecaster(Forecaster):
    """
    TimesFM foundation model forecaster with graceful degradation to statistical baselines.
    Outputs point forecast and 10th/50th/90th quantiles with monotonicity guarantee.
    """

    def __init__(
        self,
        freq: str = "D",
        series_class: str = "smooth",
        season_length: int = 7,
    ):
        super().__init__(name=f"TimesFM-{settings.TIMESFM_VERSION}")
        self.freq = freq
        self.series_class = series_class
        self.season_length = season_length
        self.y_history: Optional[np.ndarray] = None
        self.last_date = None
        self.fallback_model: Optional[Forecaster] = None

    def fit(self, df: pl.DataFrame, target_col: str = "quantity", date_col: str = "date") -> "TimesFMForecaster":
        self.target_col = target_col
        self.date_col = date_col
        df_sorted = df.sort(date_col)
        self.y_history = df_sorted[target_col].to_numpy().astype(np.float32)
        self.last_date = df_sorted[date_col].max()

        # Context window handling:
        # Max context: 512 for 2.5, 2048 for 3.0
        max_context = 2048 if settings.TIMESFM_VERSION.startswith("3") else 512
        if len(self.y_history) > max_context:
            self.y_history = self.y_history[-max_context:]

        # If series is shorter than required minimum (< 14), prepare fallback
        if len(self.y_history) < 14:
            self._init_fallback(df)

        return self

    def _init_fallback(self, df: pl.DataFrame):
        if self.series_class in ("intermittent", "lumpy"):
            self.fallback_model = CrostonForecaster(variant="sba")
        else:
            self.fallback_model = AutoETSForecaster(season_length=self.season_length)
        self.fallback_model.fit(df, target_col=self.target_col, date_col=self.date_col)

    def predict(
        self,
        horizon: int = 28,
        quantiles: Optional[List[float]] = None,
    ) -> ForecastPrediction:
        quantiles = quantiles or [0.1, 0.5, 0.9]

        if self.fallback_model is not None:
            pred = self.fallback_model.predict(horizon=horizon, quantiles=quantiles)
            pred.model_name = self.name + " (Fallback: " + self.fallback_model.name + ")"
            return pred

        model = get_timesfm_model()
        if model is None:
            # Fallback to statistical model
            if self.series_class in ("intermittent", "lumpy"):
                fb = CrostonForecaster(variant="sba")
            else:
                fb = AutoETSForecaster(season_length=self.season_length)
            
            dummy_df = pl.DataFrame({
                self.target_col: self.y_history,
                self.date_col: pl.date_range(pl.date(2025, 1, 1), pl.date(2025, 1, 1) + pl.duration(days=len(self.y_history) - 1), eager=True),
            })
            fb.fit(dummy_df, target_col=self.target_col, date_col=self.date_col)
            pred = fb.predict(horizon=horizon, quantiles=quantiles)
            pred.model_name = self.name + " (Fallback: " + fb.name + ")"
            return pred

        try:
            # Frequency indicator: 0 for Daily, 1 for Weekly, 2 for Monthly
            freq_indicator = 0
            if self.freq.upper().startswith("W"):
                freq_indicator = 1
            elif self.freq.upper().startswith("M"):
                freq_indicator = 2

            inputs = [self.y_history.tolist()]
            # Call forecast
            if hasattr(model, "forecast"):
                point_fc, q_fc = model.forecast(
                    inputs,
                    freq=[freq_indicator],
                    forecast_context_len=len(self.y_history),
                )
            elif hasattr(model, "predict"):
                point_fc, q_fc = model.predict(
                    inputs,
                    freq=[freq_indicator],
                )
            else:
                raise RuntimeError("TimesFM model instance has neither forecast nor predict method")

            pt = np.asarray(point_fc[0][:horizon], dtype=float)
            pt = np.maximum(0.0, pt)

            # Quantile extraction
            q_dict: Dict[float, List[float]] = {}
            if q_fc is not None and len(q_fc) > 0:
                raw_q = np.asarray(q_fc[0][:horizon], dtype=float)
                # Ensure monotonicity
                raw_q = np.sort(raw_q, axis=-1)
                q10 = np.maximum(0.0, raw_q[:, 1]) if raw_q.shape[1] > 1 else pt * 0.8
                q50 = np.maximum(0.0, raw_q[:, 5]) if raw_q.shape[1] > 5 else pt
                q90 = np.maximum(0.0, raw_q[:, 9]) if raw_q.shape[1] > 9 else pt * 1.2
            else:
                q10 = np.maximum(0.0, pt * 0.8)
                q50 = pt
                q90 = np.maximum(0.0, pt * 1.2)

            # Monotonicity check
            q50 = np.maximum(q10, q50)
            q90 = np.maximum(q50, q90)

            q_dict[0.1] = q10.tolist()
            q_dict[0.5] = q50.tolist()
            q_dict[0.9] = q90.tolist()

            return ForecastPrediction(
                model_name=self.name,
                point_forecast=pt.tolist(),
                quantiles=q_dict,
                prediction_dates=[],
            )

        except Exception as e:
            logger.warning(f"TimesFM inference failed ({e}), falling back to baseline.")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            fb = AutoETSForecaster(season_length=self.season_length)
            dummy_df = pl.DataFrame({
                self.target_col: self.y_history,
                self.date_col: pl.date_range(pl.date(2025, 1, 1), pl.date(2025, 1, 1) + pl.duration(days=len(self.y_history) - 1), eager=True),
            })
            fb.fit(dummy_df, target_col=self.target_col, date_col=self.date_col)
            pred = fb.predict(horizon=horizon, quantiles=quantiles)
            pred.model_name = self.name + " (Fallback: " + fb.name + ")"
            return pred
