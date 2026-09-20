from typing import Optional

from pydantic import BaseModel, Field


class SettingsResponse(BaseModel):
    environment: str
    ollama_url: str
    ollama_model: str
    timesfm_version: str
    timesfm_repo: str
    timesfm_license_warning: str
    hf_token_configured: bool
    hf_hub_offline: int
    default_country: str
    default_service_level: float
    default_lead_time_days: int
    default_review_period_days: int
    default_horizon: int


class SettingsUpdateRequest(BaseModel):
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    timesfm_version: Optional[str] = Field(
        None,
        description="Must be '3.0' or '2.5'. 3.0 requires acknowledging non-commercial license.",
    )
    default_country: Optional[str] = None
    default_service_level: Optional[float] = None
    default_lead_time_days: Optional[int] = None
    default_review_period_days: Optional[int] = None
    default_horizon: Optional[int] = None
