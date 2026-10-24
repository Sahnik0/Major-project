from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ColumnSample(BaseModel):
    name: str
    dtype: str
    sample_values: List[Any]


class ColumnMappingSuggestion(BaseModel):
    column_name: str
    mapped_field: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""


class DatasetUploadResponse(BaseModel):
    id: str
    name: str
    original_filename: str
    status: str
    detected_columns: List[str]
    sample_rows: List[Dict[str, Any]]
    suggested_mapping: Dict[str, Any]
    mapping_confidence: Dict[str, float]
    mapping_reasons: Dict[str, str]
    job_id: Optional[str] = None
    created_at: datetime


class DatasetResponse(BaseModel):
    id: str
    name: str
    original_filename: str
    status: str
    row_count: Optional[int] = None
    product_count: Optional[int] = None
    column_mapping: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str] = None


class DatasetListResponse(BaseModel):
    datasets: List[DatasetResponse]
    total: int
