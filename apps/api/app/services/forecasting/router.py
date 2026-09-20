from typing import List

from apps.api.app.services.forecasting.base import Forecaster
from apps.api.app.services.forecasting.baselines import (
    AutoARIMAForecaster,
    AutoETSForecaster,
    CrostonForecaster,
    HistoricAverageForecaster,
    NaiveForecaster,
    SeasonalNaiveForecaster,
    TSBForecaster,
)


def get_candidate_models_for_class(
    series_class: str,
    history_length: int,
    season_length: int = 7,
    include_timesfm: bool = False,
) -> List[Forecaster]:
    """
    Returns the list of candidate model instances suitable for the given series class.
    Follows Section 5.6 of the specification:
    - smooth / erratic: Seasonal Naive, AutoETS, AutoARIMA (+ TimesFM)
    - intermittent / lumpy: CrostonClassic, CrostonSBA, TSB, Naive (+ TimesFM)
    - short_history: Naive, Historic Average, Seasonal Naive (if history >= 2 seasons)
    - inactive: Empty list (no forecast)
    """
    candidates: List[Forecaster] = []

    if series_class in ("smooth", "erratic"):
        candidates.append(SeasonalNaiveForecaster(season_length=season_length))
        candidates.append(AutoETSForecaster(season_length=season_length))
        # Keep AutoARIMA as candidate if history is sufficient
        if history_length >= 2 * season_length:
            candidates.append(AutoARIMAForecaster(season_length=season_length))
        candidates.append(NaiveForecaster())

    elif series_class in ("intermittent", "lumpy"):
        candidates.append(CrostonForecaster(variant="classic"))
        candidates.append(CrostonForecaster(variant="sba"))
        candidates.append(TSBForecaster())
        candidates.append(NaiveForecaster())

    elif series_class == "short_history":
        candidates.append(NaiveForecaster())
        candidates.append(HistoricAverageForecaster())
        if history_length >= 2 * season_length:
            candidates.append(SeasonalNaiveForecaster(season_length=season_length))

    elif series_class == "inactive":
        # No forecast for inactive/discontinued products
        return []

    else:
        # Default fallback
        candidates.append(SeasonalNaiveForecaster(season_length=season_length))
        candidates.append(NaiveForecaster())

    return candidates
