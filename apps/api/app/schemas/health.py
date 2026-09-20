from datetime import UTC, datetime

from pydantic import BaseModel, Field


# System Health schemas
class SystemHealthResponse(BaseModel):
    status: str = "ok"
    device: str = "cpu"
    device_name: str | None = None
    cuda_available: bool = False
    vram_total_mb: float | None = None
    vram_allocated_mb: float | None = None
    ollama_ready: bool = False
    ollama_model: str | None = None
    timesfm_ready: bool = False
    timesfm_version: str = "3.0"
    timesfm_license_warning: str | None = None
    disk_free_gb: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Data Health Report schemas
class ProductClassStats(BaseModel):
    product: str
    series_class: str  # smooth, erratic, intermittent, lumpy, short_history, inactive
    adi: float
    cv2: float
    history_length_periods: int
    first_sale_date: str | None = None
    last_sale_date: str | None = None
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
    date_min: str | None = None
    date_max: str | None = None
    total_days: int
    granularity: str

    class_distribution: dict[str, int]  # count of products per class
    issues: list[DataQualityIssue]
    products: list[ProductClassStats]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
