from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ColumnSample(BaseModel):
    name: str
    dtype: str
    sample_values: list[Any]


class ColumnMappingSuggestion(BaseModel):
    column_name: str
    mapped_field: str | None = None
    confidence: float = 0.0
    reason: str = ""


class DatasetUploadResponse(BaseModel):
    id: str
    name: str
    original_filename: str
    status: str
    detected_columns: list[str]
    sample_rows: list[dict[str, Any]]
    suggested_mapping: dict[str, str]
    mapping_confidence: dict[str, float]
    mapping_reasons: dict[str, str]
    created_at: datetime


class DatasetResponse(BaseModel):
    id: str
    name: str
    original_filename: str
    status: str
    row_count: int | None = None
    product_count: int | None = None
    column_mapping: dict[str, str] | None = None
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None


class DatasetListResponse(BaseModel):
    datasets: list[DatasetResponse]
    total: int
