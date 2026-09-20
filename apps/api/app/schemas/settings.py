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
    ollama_url: str | None = None
    ollama_model: str | None = None
    timesfm_version: str | None = Field(
        None,
        description="Must be '3.0' or '2.5'. 3.0 requires acknowledging non-commercial license.",
    )
    default_country: str | None = None
    default_service_level: float | None = None
    default_lead_time_days: int | None = None
    default_review_period_days: int | None = None
    default_horizon: int | None = None
