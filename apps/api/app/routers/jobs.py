from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.db import Job, get_db
from apps.api.app.schemas.job import JobResponse

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("/{job_id}", response_model=JobResponse)
def get_job_status(job_id: str, db: Session = Depends(get_db)) -> JobResponse:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "JOB_NOT_FOUND",
                "message": f"Job with id '{job_id}' was not found.",
                "hint": "Check that the job ID was returned from a previous operation.",
            },
        )
    return JobResponse(
        id=job.id,
        dataset_id=job.dataset_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        current_step=job.current_step,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
