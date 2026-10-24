import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from apps.api.app.config import settings
from apps.api.app.db import Dataset, Job, get_db
from apps.api.app.db import ChatMessage, Dataset, Job, ProductForecast, get_db
from apps.api.app.jobs.runner import enqueue_cleaning_and_profiling
from apps.api.app.schemas.dataset import (
    DatasetListResponse,
    DatasetResponse,
    DatasetUploadResponse,
)
from apps.api.app.schemas.health import DataHealthReport
from apps.api.app.schemas.mapping import (
    MappingConfirmationRequest,
    MappingConfirmationResponse,
)
from apps.api.app.services.column_mapping import suggest_column_mapping_heuristics
from apps.api.app.services.ingestion import (
    inspect_raw_file,
    read_raw_dataframe,
    save_upload_stream,
)

router = APIRouter(prefix="/datasets", tags=["Datasets"])


@router.post("", response_model=DatasetUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> DatasetUploadResponse:
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_FILENAME",
                "message": "Uploaded file must have a filename.",
                "hint": "Check the file upload form.",
            },
        )

    suffix = Path(file.filename).suffix.lower()
    if suffix not in [".csv", ".xlsx", ".xls"]:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_FILE_TYPE",
                "message": f"File type '{suffix}' is not supported. Please upload a .csv or .xlsx file.",
                "hint": "Ensure your file is in standard CSV or Excel format.",
            },
        )

    dataset_id = str(uuid.uuid4())
    dataset_name = name.strip() if name and name.strip() else Path(file.filename).stem
    safe_filename = f"{dataset_id}_{file.filename}"
    dest_path = settings.UPLOAD_DIR / safe_filename

    # Save streamed upload
    await save_upload_stream(file, dest_path)

    # Read and inspect raw file
    try:
        raw_df = read_raw_dataframe(dest_path)
        detected_columns, sample_rows, total_rows, meta = inspect_raw_file(dest_path, include_meta=True)
    except Exception as e:
        if dest_path.exists():
            dest_path.unlink()
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CORRUPTED_FILE",
                "message": f"Failed to parse uploaded dataset: {str(e)}",
                "hint": "Ensure the file is not corrupted and has valid headers.",
            },
        )

    # Generate heuristic & AI column mapping suggestions
    suggested_mapping, confidences, reasons = suggest_column_mapping_heuristics(raw_df, meta=meta)

    has_required = all(k in suggested_mapping for k in ("date", "product", "quantity"))
    status = "processing" if has_required else "mapping_pending"

    # Create dataset record
    dataset = Dataset(
        id=dataset_id,
        name=dataset_name,
        original_filename=file.filename,
        file_path=str(dest_path),
        status=status,
        row_count=total_rows,
        column_mapping=suggested_mapping,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    job_id = None
    if has_required:
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            dataset_id=dataset_id,
            job_type="cleaning_profiling",
            status="queued",
            progress=5,
            current_step="Columns understood by AI. Cleaning and regularizing data...",
        )
        db.add(job)
        db.commit()

        await enqueue_cleaning_and_profiling(
            dataset_id=dataset_id,
            job_id=job_id,
            mapping_config_dict={
                "mapping": suggested_mapping,
                "date_format": None,
                "drop_duplicates": False,
                "returns_handling": "net",
                "aggregation_granularity": "daily",
            },
        )

    return DatasetUploadResponse(
        id=dataset.id,
        name=dataset.name,
        original_filename=dataset.original_filename,
        status=dataset.status,
        detected_columns=detected_columns,
        sample_rows=sample_rows,
        suggested_mapping=suggested_mapping,
        mapping_confidence=confidences,
        mapping_reasons=reasons,
        job_id=job_id,
        created_at=dataset.created_at,
    )


@router.get("", response_model=DatasetListResponse)
def list_datasets(db: Session = Depends(get_db)) -> DatasetListResponse:
    datasets = db.query(Dataset).order_by(Dataset.created_at.desc()).all()
    return DatasetListResponse(
        datasets=[
            DatasetResponse(
                id=d.id,
                name=d.name,
                original_filename=d.original_filename,
                status=d.status,
                row_count=d.row_count,
                product_count=d.product_count,
                column_mapping=d.column_mapping,
                created_at=d.created_at,
                updated_at=d.updated_at,
                error_message=d.error_message,
            )
            for d in datasets
        ],
        total=len(datasets),
    )


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)) -> DatasetResponse:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DATASET_NOT_FOUND",
                "message": f"Dataset '{dataset_id}' was not found.",
                "hint": "Check the dataset ID.",
            },
        )
    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        original_filename=dataset.original_filename,
        status=dataset.status,
        row_count=dataset.row_count,
        product_count=dataset.product_count,
        column_mapping=dataset.column_mapping,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
        error_message=dataset.error_message,
    )


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)) -> None:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DATASET_NOT_FOUND",
                "message": f"Dataset '{dataset_id}' was not found.",
                "hint": "Check the dataset ID.",
            },
        )

    # Delete physical files
    if dataset.file_path and Path(dataset.file_path).exists():
        Path(dataset.file_path).unlink()
    if dataset.parquet_path and Path(dataset.parquet_path).exists():
        Path(dataset.parquet_path).unlink()

    db.delete(dataset)
    db.commit()


@router.put("/{dataset_id}/mapping", response_model=MappingConfirmationResponse)
async def confirm_mapping(
    dataset_id: str,
    req: MappingConfirmationRequest,
    db: Session = Depends(get_db),
) -> MappingConfirmationResponse:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DATASET_NOT_FOUND",
                "message": f"Dataset '{dataset_id}' was not found.",
                "hint": "Upload a dataset before confirming mapping.",
            },
        )

    # Validate required mapping fields
    required_fields = ["date", "product", "quantity"]
    for field in required_fields:
        if field not in req.mapping or not req.mapping[field]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "MISSING_MAPPING_FIELD",
                    "message": f"Required logical field '{field}' is missing from the mapping.",
                    "hint": f"Map source columns for {', '.join(required_fields)}.",
                },
            )

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        dataset_id=dataset_id,
        job_type="cleaning_profiling",
        status="queued",
        progress=0,
        current_step="Queued for cleaning and profiling",
    )
    db.add(job)
    dataset.status = "processing"
    dataset.column_mapping = req.mapping
    db.commit()

    # Launch background cleaning and profiling task
    await enqueue_cleaning_and_profiling(
        dataset_id=dataset_id,
        job_id=job_id,
        mapping_config_dict=req.model_dump(),
    )

    return MappingConfirmationResponse(
        dataset_id=dataset_id,
        job_id=job_id,
        status="queued",
        message="Dataset mapping confirmed. Cleaning and health profiling started.",
    )


@router.get("/{dataset_id}/health", response_model=DataHealthReport)
def get_dataset_health(dataset_id: str, db: Session = Depends(get_db)) -> DataHealthReport:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DATASET_NOT_FOUND",
                "message": f"Dataset '{dataset_id}' was not found.",
                "hint": "Check the dataset ID.",
            },
        )

    if dataset.status == "processing":
        raise HTTPException(
            status_code=202,
            detail={
                "code": "PROCESSING",
                "message": "Dataset cleaning and health profiling is currently in progress.",
                "hint": "Poll the job status endpoint /jobs/{id} until completed.",
            },
        )

    if not dataset.health_report:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "HEALTH_REPORT_UNAVAILABLE",
                "message": f"Data health report is not available for dataset status '{dataset.status}'.",
                "hint": "Confirm column mapping to trigger dataset processing.",
            },
        )

    return DataHealthReport(**dataset.health_report)


@router.post("/sample", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def load_sample_dataset(db: Session = Depends(get_db)) -> DatasetResponse:
    from apps.api.app.services.cleaning import clean_and_regularize_dataset
    from apps.api.app.services.profiling import generate_data_health_report
    from scripts.generate_sample_data import generate_dataset

    sample_csv = settings.DATA_DIR / "sample_sales.csv"
    if not sample_csv.exists():
        generate_dataset()

    dataset_id = str(uuid.uuid4())
    dest_path = settings.UPLOAD_DIR / f"{dataset_id}_sample_sales.csv"
    import shutil
    shutil.copy(sample_csv, dest_path)

    raw_df = read_raw_dataframe(dest_path)
    mapping_req = MappingConfirmationRequest(
        mapping={
            "date": "Date",
            "product": "Product_SKU",
            "quantity": "Units_Sold",
            "revenue": "Revenue",
            "store": "Store_ID",
            "promo_flag": "Promo_Flag",
        },
        date_format="iso",
        drop_duplicates=False,
        returns_handling="net",
        aggregation_granularity="daily",
    )

    cleaned_df, cleaning_meta = clean_and_regularize_dataset(
        df=raw_df,
        mapping_config=mapping_req,
        dataset_id=dataset_id,
    )

    health_report = generate_data_health_report(
        cleaned_df=cleaned_df,
        dataset_id=dataset_id,
        dataset_name="Demo Retail Store (40 Products)",
        cleaning_meta=cleaning_meta,
    )

    dataset = Dataset(
        id=dataset_id,
        name="Demo Retail Store (40 Products)",
        original_filename="sample_sales.csv",
        file_path=str(dest_path),
        parquet_path=cleaning_meta["parquet_path"],
        status="ready",
        row_count=cleaning_meta["cleaned_row_count"],
        product_count=cleaning_meta["unique_products"],
        column_mapping=mapping_req.mapping,
        health_report=health_report.model_dump(mode="json"),
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        original_filename=dataset.original_filename,
        status=dataset.status,
        row_count=dataset.row_count,
        product_count=dataset.product_count,
        column_mapping=dataset.column_mapping,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
        error_message=dataset.error_message,
    )


@router.get("/{dataset_id}/summary")
async def get_dataset_business_summary(dataset_id: str, db: Session = Depends(get_db)):
    from apps.api.app.services.insights import build_business_facts
    from apps.api.app.services.narrator import generate_business_narrative

    facts = build_business_facts(dataset_id, db)
    if not facts:
        return {"facts": {}, "narrative": "No forecast data available yet. Please run a forecast job first."}

    narrative = await generate_business_narrative(facts)
    return {"facts": facts, "narrative": narrative}


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": f"Dataset '{dataset_id}' not found."},
        )

    # Clean up associated database records explicitly
    db.query(ChatMessage).filter(ChatMessage.dataset_id == dataset_id).delete(synchronize_session=False)
    db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset_id).delete(synchronize_session=False)
    db.query(Job).filter(Job.dataset_id == dataset_id).delete(synchronize_session=False)

    # Clean up files on disk if they exist
    if dataset.file_path:
        f_p = Path(dataset.file_path)
        if f_p.exists() and "uploads" in str(f_p):
            try:
                f_p.unlink(missing_ok=True)
            except Exception:
                pass
    if dataset.parquet_path:
        p_p = Path(dataset.parquet_path)
        if p_p.exists():
            try:
                p_p.unlink(missing_ok=True)
            except Exception:
                pass

    db.delete(dataset)
    db.commit()
    return {"status": "ok", "message": f"Dataset '{dataset_id}' deleted."}


