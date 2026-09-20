from typing import Dict, Optional

from pydantic import BaseModel, Field


class MappingConfirmationRequest(BaseModel):
    mapping: Dict[str, str] = Field(
        ...,
        description="Dictionary of logical field name to source column name. Must include 'date', 'product', 'quantity'.",
    )
    date_format: Optional[str] = Field(
        default=None,
        description="Optional date parsing preference ('dayfirst', 'monthfirst', or explicit format).",
    )
    drop_duplicates: bool = Field(
        default=False,
        description="Whether to drop exact duplicate rows. Default False (warn, don't drop).",
    )
    returns_handling: str = Field(
        default="net",
        description="How to handle negative quantities: 'net' (net-of-returns) or 'separate'.",
    )
    cap_outliers: bool = Field(
        default=False,
        description="Whether to cap outliers to robust boundaries instead of leaving them.",
    )
    aggregation_granularity: str = Field(
        default="daily",
        description="Aggregation time grid: 'daily' or 'weekly'.",
    )
    aggregate_stores: bool = Field(
        default=True,
        description="If a store column is mapped, whether to aggregate across stores (True) or forecast per (store, product) (False).",
    )


class MappingConfirmationResponse(BaseModel):
    dataset_id: str
    job_id: str
    status: str
    message: str
