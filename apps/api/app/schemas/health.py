from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# System Health schemas
class SystemHealthResponse(BaseModel):
    status: str = "ok"
    device: str = "cpu"
    device_name: Optional[str] = None
    cuda_available: bool = False
    vram_total_mb: Optional[float] = None
    vram_allocated_mb: Optional[float] = None
    ollama_ready: bool = False
    ollama_model: Optional[str] = None
    timesfm_ready: bool = False
    timesfm_version: str = "3.0"
    timesfm_license_warning: Optional[str] = None
    disk_free_gb: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Data Health Report schemas
class ProductClassStats(BaseModel):
    product: str
    series_class: str  # smooth, erratic, intermittent, lumpy, short_history, inactive
    adi: float
    cv2: float
    history_length_periods: int
    first_sale_date: Optional[str] = None
    last_sale_date: Optional[str] = None
    total_sales_units: float
    active_days: int
    zero_demand_ratio: float
    outlier_count: int


class DataQualityIssue(BaseModel):
    issue_type: str  # duplicates, missing_values, outliers, gaps, negative_values
    severity: str  # info, warning, error
    title: str
    description: str
    action_taken: str
    affected_count: int


class DataHealthReport(BaseModel):
    dataset_id: str
    dataset_name: str
    total_rows_raw: int
    total_rows_cleaned: int
    total_products: int
    date_min: Optional[str] = None
    date_max: Optional[str] = None
    total_days: int
    granularity: str

    class_distribution: Dict[str, int]  # count of products per class
    issues: List[DataQualityIssue]
    products: List[ProductClassStats]
    dataset_type: Optional[str] = None
    stock_health: Optional[Dict[str, int]] = None
    top_out_of_stock: Optional[List[Dict[str, Any]]] = None
    top_critical: Optional[List[Dict[str, Any]]] = None
    top_moving: Optional[List[Dict[str, Any]]] = None
    total_stock_units: Optional[float] = None
    total_stock_value: Optional[float] = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
