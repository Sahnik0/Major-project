from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import polars as pl


@dataclass
class ForecastPrediction:
    dates: List[str]
    point_forecast: List[float]
    quantiles: Dict[float, List[float]] = field(default_factory=dict)
    model_name: str = "Unknown"
    confidence_label: str = "normal"  # "normal" or "low_confidence"


class Forecaster(ABC):
    name: str = "BaseForecaster"
    def __init__(self, name: str = "BaseForecaster"):
        self.name = name

    @abstractmethod
    def fit(
        self,
        df: pl.DataFrame,
        target_col: str = "quantity",
        date_col: str = "date",
    ) -> "Forecaster":
        """Fits the forecasting model on historical series."""
        pass

    @abstractmethod
    def predict(
        self,
        horizon: int,
        quantiles: Optional[List[float]] = None,
    ) -> ForecastPrediction:
        """Generates future predictions with point forecasts and quantile intervals."""
        pass

