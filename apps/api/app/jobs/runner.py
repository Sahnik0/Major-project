import asyncio
from pathlib import Path
from typing import Any, Dict

from sqlalchemy.orm import Session

from apps.api.app.db import Dataset, Job, SessionLocal
from apps.api.app.schemas.mapping import MappingConfirmationRequest
from apps.api.app.services.cleaning import clean_and_regularize_dataset
from apps.api.app.services.ingestion import read_raw_dataframe
from apps.api.app.services.profiling import generate_data_health_report


def run_cleaning_and_profiling_sync(
    dataset_id: str,
    job_id: str,
    mapping_config_dict: Dict[str, Any],
) -> None:
    """Synchronous worker that performs file reading, cleaning, profiling, and state updates."""
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()

        if not job or not dataset:
            return

        job.status = "processing"
        job.progress = 10
        job.current_step = "Reading raw dataset file"
        db.commit()

        raw_df = read_raw_dataframe(Path(dataset.file_path))

        job.progress = 30
        job.current_step = "Cleaning, parsing dates, and regularizing series"
        db.commit()

        req = MappingConfirmationRequest(**mapping_config_dict)
        cleaned_df, cleaning_meta = clean_and_regularize_dataset(
            df=raw_df,
            mapping_config=req,
            dataset_id=dataset_id,
        )

        job.progress = 70
        job.current_step = "Classifying demand patterns and computing health report"
        db.commit()

        health_report = generate_data_health_report(
            cleaned_df=cleaned_df,
            dataset_id=dataset_id,
            dataset_name=dataset.name,
            cleaning_meta=cleaning_meta,
        )

        job.progress = 95
        job.current_step = "Saving results"
        db.commit()

        # Update dataset record
        dataset.status = "ready"
        # Update dataset record with cleaning metadata
        dataset.status = "processing"
        dataset.parquet_path = cleaning_meta["parquet_path"]
        dataset.row_count = cleaning_meta["cleaned_row_count"]
        dataset.product_count = cleaning_meta["unique_products"]
        dataset.column_mapping = req.mapping
        dataset.health_report = health_report.model_dump(mode="json")

        # Mark job completed
        job.status = "completed"
        job.progress = 100
        job.current_step = "Completed"
        job.progress = 25
        job.current_step = "Data regularized & profiled. Starting TimesFM & baseline evaluations..."
        db.commit()

        # Automatically run TimesFM & baseline forecasting and inventory sizing with the active job
        try:
            from apps.api.app.jobs.forecast_runner import run_forecast_job_sync

            run_forecast_job_sync(
                dataset_id=dataset_id,
                job_id=job_id,
                params_dict={
                    "horizon": 28,
                    "num_folds": 2,
                    "service_level": 0.90,
                    "lead_time_days": 7,
                    "review_period_days": 7,
                    "include_timesfm": True,
                },
            )
        except Exception as f_err:
            logger.warning(f"Auto-forecasting error: {f_err}")
            dataset.status = "ready"
            job.status = "completed"
            job.progress = 100
            job.current_step = "Data cleaned and health profiled (forecasting skipped)"
            db.commit()

    except Exception as e:
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            job.current_step = f"Error: {str(e)}"
        if dataset:
            dataset.status = "error"
            dataset.error_message = str(e)
        db.commit()
    finally:
        db.close()


async def enqueue_cleaning_and_profiling(
    dataset_id: str,
    job_id: str,
    mapping_config_dict: Dict[str, Any],
) -> None:
    """Run cleaning & profiling asynchronously in a thread pool."""
    asyncio.create_task(
        asyncio.to_thread(
            run_cleaning_and_profiling_sync,
            dataset_id,
            job_id,
            mapping_config_dict,
        )
    )
