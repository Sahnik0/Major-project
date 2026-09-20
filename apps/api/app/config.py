from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ENVIRONMENT: str = "development"

    # Base paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOAD_DIR: Path = BASE_DIR / "data" / "uploads"
    PARQUET_DIR: Path = BASE_DIR / "data" / "parquet"
    SQLITE_DIR: Path = BASE_DIR / "data" / "sqlite"
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/sqlite/advisor.db"

    # Ollama & LLM
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma3:4b"

    # TimesFM
    TIMESFM_VERSION: str = "3.0"
    TIMESFM_REPO: str = "google/timesfm-3.0-pytorch"
    HF_TOKEN: Optional[str] = None
    HF_HUB_OFFLINE: int = 0

    # Ingestion & limits
    MAX_UPLOAD_SIZE_MB: int = 100

    # Business & profiling defaults
    DEFAULT_COUNTRY: str = "IN"
    DEFAULT_SERVICE_LEVEL: float = 0.90
    DEFAULT_LEAD_TIME_DAYS: int = 7
    DEFAULT_REVIEW_PERIOD_DAYS: int = 7
    DEFAULT_HORIZON: int = 28
    MIN_SERIES_LENGTH_DAILY: int = 60
    MIN_SERIES_LENGTH_WEEKLY: int = 8
    INACTIVE_THRESHOLD_DAYS: int = 60

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# Ensure directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.PARQUET_DIR.mkdir(parents=True, exist_ok=True)
settings.SQLITE_DIR.mkdir(parents=True, exist_ok=True)
