import csv
import io
import uuid
from pathlib import Path
from typing import List, Optional

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from apps.api.app.db import Dataset, Job, ProductForecast, get_db
from apps.api.app.jobs.forecast_runner import enqueue_forecast_job
from apps.api.app.schemas.forecast import (
    ForecastJobCreateRequest,
    ForecastJobCreateResponse,
    InventoryAdviceItem,
    InventoryListResponse,
    ProductDetailResponse,
    ProductListResponse,
    ProductSummaryItem,
)

router = APIRouter(prefix="/datasets", tags=["Forecasts & Inventory"])


@router.post(
    "/{dataset_id}/forecast-jobs",
    response_model=ForecastJobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_forecast_job(
    dataset_id: str,
    req: ForecastJobCreateRequest,
    db: Session = Depends(get_db),
) -> ForecastJobCreateResponse:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DATASET_NOT_FOUND",
                "message": f"Dataset '{dataset_id}' not found.",
                "hint": "Upload a dataset first.",
            },
        )

    if dataset.status != "ready":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "DATASET_NOT_READY",
                "message": f"Dataset status is '{dataset.status}'. Must be 'ready' before generating forecasts.",
                "hint": "Wait for dataset cleaning and health profiling to complete.",
            },
        )

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        dataset_id=dataset_id,
        job_type="forecast_backtest",
        status="queued",
        progress=0,
        current_step="Queued for forecasting and backtesting",
    )
    db.add(job)
    db.commit()

    await enqueue_forecast_job(
        dataset_id=dataset_id,
        job_id=job_id,
        params_dict=req.model_dump(),
    )

    return ForecastJobCreateResponse(
        job_id=job_id,
        dataset_id=dataset_id,
        status="queued",
        message="Forecast and inventory job successfully initiated.",
    )


@router.get("/{dataset_id}/products", response_model=ProductListResponse)
def list_products(
    dataset_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    series_class: Optional[str] = Query(default=None),
    stockout_risk: Optional[str] = Query(default=None),
    sort_by: str = Query(default="product", pattern="^(product|total_demand|wape|stockout_risk)$"),
    sort_desc: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> ProductListResponse:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": "Dataset not found", "hint": ""},
        )

    forecasts = db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset_id).all()
    if not forecasts:
        return ProductListResponse(products=[], total=0, page=page, page_size=page_size)

    items: List[ProductSummaryItem] = []
    for pf in forecasts:
        if series_class and pf.series_class != series_class:
            continue

        inv = pf.inventory_advice or {}
        risk = inv.get("stockout_risk", "unknown")
        if stockout_risk and risk != stockout_risk:
            continue

        pts = pf.point_forecast
        total_demand = round(float(sum(pts)), 2) if pts else 0.0

        # Extract winning model WAPE from backtest table
        wape_val = None
        if pf.backtest_table:
            for b in pf.backtest_table:
                if b.get("model_name") == pf.chosen_model:
                    wape_val = b.get("wape")
                    break

        items.append(
            ProductSummaryItem(
                product=pf.product_id,
                series_class=pf.series_class,
                chosen_model=pf.chosen_model,
                total_forecast_demand=total_demand,
                stockout_risk=risk,
                days_of_cover=inv.get("days_of_cover"),
                recommended_order_qty=inv.get("recommended_order_qty"),
                wape=wape_val,
            )
        )

    # Sorting
    if sort_by == "product":
        items.sort(key=lambda x: x.product, reverse=sort_desc)
    elif sort_by == "total_demand":
        items.sort(key=lambda x: x.total_forecast_demand, reverse=sort_desc)
    elif sort_by == "wape":
        items.sort(key=lambda x: (x.wape is None, x.wape), reverse=sort_desc)
    elif sort_by == "stockout_risk":
        risk_order = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
        items.sort(key=lambda x: risk_order.get(x.stockout_risk, 0), reverse=sort_desc)

    total_count = len(items)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated = items[start_idx:end_idx]

    return ProductListResponse(
        products=paginated,
        total=total_count,
        page=page,
        page_size=page_size,
    )


@router.get("/{dataset_id}/products/{product_id}", response_model=ProductDetailResponse)
def get_product_detail(
    dataset_id: str,
    product_id: str,
    db: Session = Depends(get_db),
) -> ProductDetailResponse:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": "Dataset not found", "hint": ""},
        )

    pf = (
        db.query(ProductForecast)
        .filter(ProductForecast.dataset_id == dataset_id, ProductForecast.product_id == product_id)
        .first()
    )
    if not pf:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PRODUCT_FORECAST_NOT_FOUND",
                "message": f"Forecast for product '{product_id}' was not found.",
                "hint": "Run a forecast job first.",
            },
        )

    # Load historical series from Parquet
    history_dates = []
    history_quantities = []
    if dataset.parquet_path and Path(dataset.parquet_path).exists():
        df = pl.read_parquet(dataset.parquet_path)
        p_df = df.filter(pl.col("product") == product_id).sort("date")
        history_dates = [d.isoformat() for d in p_df["date"].to_list()]
        history_quantities = [float(q) for q in p_df["quantity"].to_list()]

    return ProductDetailResponse(
        product=pf.product_id,
        dataset_id=dataset_id,
        series_class=pf.series_class,
        chosen_model=pf.chosen_model,
        history_dates=history_dates,
        history_quantities=history_quantities,
        forecast_dates=pf.forecast_dates,
        point_forecast=pf.point_forecast,
        quantiles=pf.quantiles,
        backtest_table=pf.backtest_table or [],
        inventory_advice=pf.inventory_advice or {},
        ai_explanation=None,
    )


@router.get("/{dataset_id}/inventory", response_model=InventoryListResponse)
def get_inventory_recommendations(
    dataset_id: str,
    db: Session = Depends(get_db),
) -> InventoryListResponse:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": "Dataset not found", "hint": ""},
        )

    forecasts = db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset_id).all()
    items: List[InventoryAdviceItem] = []

    for pf in forecasts:
        inv = pf.inventory_advice or {}
        items.append(
            InventoryAdviceItem(
                product=pf.product_id,
                series_class=pf.series_class,
                current_stock=inv.get("current_stock"),
                reorder_point=inv.get("reorder_point", 0.0),
                recommended_order_qty=inv.get("recommended_order_qty"),
                days_of_cover=inv.get("days_of_cover"),
                stockout_risk=inv.get("stockout_risk", "unknown"),
                overstock_flag=inv.get("overstock_flag", False),
                advice_text=inv.get("advice_text", ""),
            )
        )

    return InventoryListResponse(items=items, total_products=len(items))


@router.get("/{dataset_id}/inventory.csv")
def export_inventory_csv(
    dataset_id: str,
    db: Session = Depends(get_db),
) -> Response:
    inv_response = get_inventory_recommendations(dataset_id, db)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "product",
            "series_class",
            "current_stock",
            "reorder_point",
            "recommended_order_qty",
            "days_of_cover",
            "stockout_risk",
            "overstock_flag",
            "advice_text",
        ],
    )
    writer.writeheader()
    for item in inv_response.items:
        writer.writerow(item.model_dump())

    csv_content = output.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=inventory_{dataset_id}.csv"},
    )
