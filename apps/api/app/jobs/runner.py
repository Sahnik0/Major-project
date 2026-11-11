import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Dict

from sqlalchemy.orm import Session

from apps.api.app.db import Dataset, Job, SessionLocal
from apps.api.app.schemas.mapping import MappingConfirmationRequest
from apps.api.app.services.cleaning import clean_and_regularize_dataset
from apps.api.app.services.column_mapping import (
    build_column_profiles,
    detect_row_grain,
    explain_columns,
    generate_suggested_questions,
)
from apps.api.app.services.ingestion import read_raw_dataframe
from apps.api.app.services.profiling import generate_data_health_report

logger = logging.getLogger("advisor.jobs.runner")


def run_cleaning_and_profiling_sync(
    dataset_id: str,
    job_id: str,
    mapping_config_dict: Dict[str, Any],
    auto_forecast: bool = False,
) -> None:
    """
    Synchronous worker that performs file reading, universal schema profiling, cleaning,
    and immediately marks the dataset 'ready' so the user can proceed without waiting.
    Optionally launches a separate non-blocking forecast job for temporal/stock datasets.
    """
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()

        if not job or not dataset:
            return

        job.status = "processing"
        job.progress = 15
        job.current_step = "Reading dataset and profiling all rows & columns"
        db.commit()

        raw_df = read_raw_dataframe(Path(dataset.file_path))

        # Ensure rich schema metadata is always populated on the mapping dict
        mapping_dict = dict(mapping_config_dict.get("mapping") or {})
        existing_map = dataset.column_mapping or {}
        for meta_key in ("_dataset_type", "_date_range", "_report_name", "_composite_product_cols"):
            if meta_key not in mapping_dict and meta_key in existing_map:
                mapping_dict[meta_key] = existing_map[meta_key]

        profiles = build_column_profiles(raw_df)
        mapping_dict["_column_profiles"] = profiles
        mapping_dict["_column_semantics"] = explain_columns(raw_df.columns, df=raw_df)
        mapping_dict["_row_grain"] = detect_row_grain(
            raw_df,
            profiles,
            date_col=mapping_dict.get("date") if mapping_dict.get("date") in raw_df.columns else None,
        )
        mapping_dict["_suggested_questions"] = generate_suggested_questions(raw_df, profiles, mapping_dict)
        mapping_config_dict = {**mapping_config_dict, "mapping": mapping_dict}

        job.progress = 45
        job.current_step = "Cleaning, parsing dates, and regularizing data"
        db.commit()

        req = MappingConfirmationRequest(**mapping_config_dict)
        cleaned_df, cleaning_meta = clean_and_regularize_dataset(
            df=raw_df,
            mapping_config=req,
            dataset_id=dataset_id,
        )

        job.progress = 80
        job.current_step = "Computing data health & pattern diagnostics"
        db.commit()

        health_report = generate_data_health_report(
            cleaned_df=cleaned_df,
            dataset_id=dataset_id,
            dataset_name=dataset.name,
            cleaning_meta=cleaning_meta,
        )

        # Immediately mark dataset READY so user can query, analyze, or export right away!
        dataset.status = "ready"
        dataset.parquet_path = cleaning_meta["parquet_path"]
        dataset.row_count = cleaning_meta["cleaned_row_count"]
        dataset.product_count = cleaning_meta["unique_products"]
        dataset.column_mapping = mapping_dict
        dataset.health_report = health_report.model_dump(mode="json")
        dataset.error_message = None

        job.status = "completed"
        job.progress = 100
        job.current_step = "Dataset understood, cleaned, and ready"
        db.commit()

        # Eagerly create Step 1 schema/health briefing card
        try:
            from apps.api.app.services.chat_advisor import get_or_create_initial_messages

            get_or_create_initial_messages(dataset, db)
        except Exception as msg_err:
            logger.warning(f"Initial message creation warning: {msg_err}")

        # Optionally run background forecasting in a SEPARATE job for temporal / stock datasets
        if auto_forecast and cleaning_meta.get("dataset_type") in ("time_series", "stock_summary"):
            existing_fc_job = (
                db.query(Job)
                .filter(
                    Job.dataset_id == dataset_id,
                    Job.job_type == "forecast_backtest",
                    Job.status.in_(["queued", "processing"]),
                )
                .first()
            )
            if not existing_fc_job:
                fc_job_id = str(uuid.uuid4())
                fc_job = Job(
                    id=fc_job_id,
                    dataset_id=dataset_id,
                    job_type="forecast_backtest",
                    status="queued",
                    progress=5,
                    current_step="Evaluating forecasting models in background...",
                )
                db.add(fc_job)
                db.commit()
                try:
                    from apps.api.app.jobs.forecast_runner import run_forecast_job_sync

                    run_forecast_job_sync(
                        dataset_id=dataset_id,
                        job_id=fc_job_id,
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
                    logger.warning(f"Background auto-forecasting warning: {f_err}")

    except Exception as e:
        logger.exception(f"Cleaning/profiling job failed for dataset {dataset_id}: {e}")
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
    auto_forecast: bool = False,
) -> None:
    """Run cleaning & profiling asynchronously in a thread pool."""
    asyncio.create_task(
        asyncio.to_thread(
            run_cleaning_and_profiling_sync,
            dataset_id,
            job_id,
            mapping_config_dict,
            auto_forecast,
        )
    )
