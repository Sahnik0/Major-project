from datetime import datetime

from pydantic import BaseModel


class JobResponse(BaseModel):
    id: str
    dataset_id: str
    job_type: str
    status: str
    progress: int
    current_step: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
