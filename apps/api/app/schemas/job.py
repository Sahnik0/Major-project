from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class JobResponse(BaseModel):
    id: str
    dataset_id: str
    job_type: str
    status: str
    progress: int
    current_step: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
