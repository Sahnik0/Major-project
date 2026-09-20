from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ForecastJobCreateRequest(BaseModel):
    horizon: int = Field(default=28, ge=1, le=365, description="Forecast horizon in days/weeks")
    num_folds: int = Field(default=3, ge=1, le=10, description="Number of backtest folds")
    service_level: float = Field(
        default=0.90, ge=0.50, le=0.99, description="Target service level for safety stock"
    )
    lead_time_days: int = Field(default=7, ge=1, le=180, description="Supplier lead time in days")
    review_period_days: int = Field(
        default=7, ge=1, le=180, description="Order review period in days"
    )
    include_timesfm: bool = Field(
        default=False, description="Include TimesFM in candidate models (Phase 3)"
    )


class ForecastJobCreateResponse(BaseModel):
    job_id: str
    dataset_id: str
    status: str
    message: str


class ProductSummaryItem(BaseModel):
    product: str
    series_class: str
    chosen_model: str
    total_forecast_demand: float
    stockout_risk: str
    days_of_cover: Optional[float] = None
    recommended_order_qty: Optional[int] = None
    wape: Optional[float] = None


class ProductListResponse(BaseModel):
    products: List[ProductSummaryItem]
    total: int
    page: int
    page_size: int


class ProductDetailResponse(BaseModel):
    product: str
    dataset_id: str
    series_class: str
    chosen_model: str
    history_dates: List[str]
    history_quantities: List[float]
    forecast_dates: List[str]
    point_forecast: List[float]
    quantiles: Dict[str, List[float]]
    backtest_table: List[Dict[str, Any]]
    inventory_advice: Dict[str, Any]
    ai_explanation: Optional[str] = None


class InventoryAdviceItem(BaseModel):
    product: str
    series_class: str
    current_stock: Optional[float] = None
    reorder_point: float
    recommended_order_qty: Optional[int] = None
    days_of_cover: Optional[float] = None
    stockout_risk: str
    overstock_flag: bool
    advice_text: str


class InventoryListResponse(BaseModel):
    items: List[InventoryAdviceItem]
    total_products: int
