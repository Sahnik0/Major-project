import asyncio
from pathlib import Path
from typing import Any, Dict

import numpy as np
import polars as pl
from sqlalchemy.orm import Session

from apps.api.app.db import Dataset, Job, ProductForecast, SessionLocal
from apps.api.app.schemas.forecast import ForecastJobCreateRequest
from apps.api.app.services.forecasting.backtest import run_rolling_origin_backtest_for_product
from apps.api.app.services.inventory import compute_inventory_advice


def run_forecast_job_sync(
    dataset_id: str,
    job_id: str,
    params_dict: Dict[str, Any],
) -> None:
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()

        if not job or not dataset:
            return

        job.status = "processing"
        job.progress = 5
        job.current_step = "Loading regularized dataset parquet"
        db.commit()

        req = ForecastJobCreateRequest(**params_dict)
        parquet_path = Path(dataset.parquet_path)
        if not parquet_path.exists():
            raise FileNotFoundError(f"Parquet file {parquet_path} not found.")

        df = pl.read_parquet(parquet_path)

        # Get product classification map from health report
        health_rep = dataset.health_report or {}
        prod_class_map = {
            p["product"]: p.get("series_class", "smooth")
            for p in health_rep.get("products", [])
        }

        # Sort products by total quantity descending so primary drivers are processed first
        vol_by_prod = (
            df.group_by("product")
            .agg(pl.col("quantity").sum().alias("total_qty"))
            .sort("total_qty", descending=True)
        )
        unique_products = vol_by_prod["product"].to_list()
        total_products = len(unique_products)

        # Delete existing forecasts for this dataset before regenerating
        db.query(ProductForecast).filter(ProductForecast.dataset_id == dataset_id).delete()
        db.commit()

        # Determine dynamic frequency and horizon from health report
        freq_code = health_rep.get("frequency_code", "D")
        if not freq_code:
            gran = health_rep.get("granularity", "daily")
            freq_code = "W" if gran == "weekly" else ("M" if gran == "monthly" else "D")

        default_horizons = {"D": 28, "W": 8, "M": 3}
        effective_horizon = req.horizon if req.horizon != 28 else default_horizons.get(freq_code, 28)

        # Speed optimization for large product catalogs (>25 items)
        max_full_eval = 25

        for idx, prod in enumerate(unique_products):
            step_progress = int(25 + (idx / total_products) * 70)
            job.progress = step_progress
            job.current_step = f"Evaluating TimesFM & baselines for {prod} ({idx+1}/{total_products})..."
            db.commit()

            p_df = df.filter(pl.col("product") == prod).sort("date")
            p_class = prod_class_map.get(str(prod), "smooth")

            if p_class == "inactive":
                # Save inactive placeholder
                pf = ProductForecast(
                    dataset_id=dataset_id,
                    product_id=str(prod),
                    series_class=p_class,
                    chosen_model="None (Inactive)",
                    point_forecast_json="[]",
                    forecast_dates_json="[]",
                    quantiles_json="{}",
                    backtest_table_json="[]",
                    inventory_advice_json=None,
                )
                pf.inventory_advice = {
                    "advice_text": "Product is inactive/discontinued (no recent sales). Forecast suppressed.",
                    "stockout_risk": "low",
                    "reorder_point": 0.0,
                    "recommended_order_qty": 0,
                    "days_of_cover": 999.0,
                    "overstock_flag": False,
                }
                db.add(pf)
                db.commit()
                continue

            # Run rolling origin backtesting: top products get TimesFM & full folds, remainder get fast statistical baselines
            eval_timesfm = req.include_timesfm if idx < max_full_eval else False
            eval_folds = min(req.num_folds, 2) if idx < max_full_eval else 1

            b_res = run_rolling_origin_backtest_for_product(
                df_product=p_df,
                product_name=str(prod),
                series_class=p_class,
                horizon=effective_horizon,
                num_folds=eval_folds,
                include_timesfm=eval_timesfm,
                freq=freq_code,
            )

            if not b_res:
                continue

            final_fc = b_res.final_forecast
            q_upper = final_fc.quantiles.get(req.service_level, final_fc.quantiles.get(0.9, None))

            hist_quantities = p_df["quantity"].to_numpy()
            hist_std = float(np.std(hist_quantities)) if len(hist_quantities) > 1 else 0.0

            # Extract actual current on-hand stock if present
            current_stock_val = None
            if "stock_on_hand" in p_df.columns:
                try:
                    s_vals = p_df["stock_on_hand"].drop_nulls()
                    if len(s_vals) > 0:
                        current_stock_val = float(s_vals[-1])
                except Exception:
                    pass

            # Compute deterministic inventory recommendations
            inv_res = compute_inventory_advice(
                product=str(prod),
                point_forecast=final_fc.point_forecast,
                quantile_upper_forecast=q_upper,
                historical_daily_std=hist_std,
                lead_time_days=req.lead_time_days,
                review_period_days=req.review_period_days,
                service_level=req.service_level,
                current_stock=current_stock_val,
            )

            # Stringify quantiles keys for JSON compatibility
            str_quantiles = {str(k): v for k, v in final_fc.quantiles.items()}

            inv_dict = {
                "product": inv_res.product,
                "lead_time_days": inv_res.lead_time_days,
                "review_period_days": inv_res.review_period_days,
                "service_level": inv_res.service_level,
                "average_daily_demand": inv_res.average_daily_demand,
                "lead_time_demand": inv_res.lead_time_demand,
                "review_period_demand": inv_res.review_period_demand,
                "safety_stock": inv_res.safety_stock,
                "reorder_point": inv_res.reorder_point,
                "target_order_level": inv_res.target_order_level,
                "recommended_order_qty": inv_res.recommended_order_qty,
                "days_of_cover": inv_res.days_of_cover,
                "stockout_risk": inv_res.stockout_risk,
                "overstock_flag": inv_res.overstock_flag,
                "advice_text": inv_res.advice_text,
            }

            pf = ProductForecast(
                dataset_id=dataset_id,
                product_id=str(prod),
                series_class=p_class,
                chosen_model=b_res.best_model_name,
            )
            pf.point_forecast = final_fc.point_forecast
            pf.forecast_dates = final_fc.dates
            pf.quantiles = str_quantiles
            pf.backtest_table = b_res.metrics_table
            pf.inventory_advice = inv_dict

            db.add(pf)
            db.commit()

        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset:
            dataset.status = "ready"
        job.status = "completed"
        job.progress = 100
        job.current_step = "Analysis complete. Predictions and inventory sizing ready."
        db.commit()

        # Generate initial decision cards and executive briefing eagerly
        try:
            from apps.api.app.services.chat_advisor import get_or_create_initial_messages
            if dataset:
                get_or_create_initial_messages(dataset, db)
        except Exception as e_msg:
            pass

    except Exception as e:
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            job.current_step = f"Error during forecasting: {str(e)}"
            db.commit()
    finally:
        db.close()


async def enqueue_forecast_job(
    dataset_id: str,
    job_id: str,
    params_dict: Dict[str, Any],
) -> None:
    asyncio.create_task(
        asyncio.to_thread(
            run_forecast_job_sync,
            dataset_id,
            job_id,
            params_dict,
        )
    )
