from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from apps.api.app.config import settings
from apps.api.app.schemas.health import (
    DataHealthReport,
    DataQualityIssue,
    ProductClassStats,
)


def calculate_syntetos_boylan(
    quantities: np.ndarray,
    granularity: str = "daily",
    min_periods: int = 60,
    dataset_max_date: Optional[date] = None,
    last_sale_date: Optional[date] = None,
    inactive_days: int = 60,
) -> Tuple[str, float, float]:
    """
    Computes Syntetos-Boylan ADI and CV^2 and returns (series_class, adi, cv2).
    Every series is assigned exactly ONE class from:
    - 'inactive'
    - 'short_history'
    - 'smooth'
    - 'erratic'
    - 'intermittent'
    - 'lumpy'
    """
    n_periods = len(quantities)
    non_zero = quantities[quantities > 0]
    k_nonzero = len(non_zero)

    # 1. Inactive check: No sales in the last N days relative to dataset end
    if k_nonzero == 0:
        return "inactive", float("inf"), 0.0

    if dataset_max_date and last_sale_date:
        days_since_last = (dataset_max_date - last_sale_date).days
        if days_since_last >= inactive_days:
            return "inactive", float(round(n_periods / k_nonzero, 3)), 0.0

    # 2. Short history check
    min_req = min_periods if granularity == "daily" else settings.MIN_SERIES_LENGTH_WEEKLY
    if n_periods < min_req:
        adi = round(float(n_periods / k_nonzero), 3) if k_nonzero > 0 else float("inf")
        cv2 = 0.0
        if k_nonzero >= 2:
            mean_val = np.mean(non_zero)
            std_val = np.std(non_zero, ddof=1)
            cv2 = round(float((std_val / mean_val) ** 2), 3) if mean_val > 0 else 0.0
        return "short_history", adi, cv2

    # 3. Calculate ADI
    adi = float(n_periods / k_nonzero)

    # 4. Calculate CV^2
    if k_nonzero < 2:
        cv2 = 0.0
    else:
        mean_val = np.mean(non_zero)
        std_val = np.std(non_zero, ddof=1)
        cv2 = float((std_val / mean_val) ** 2) if mean_val > 0 else 0.0

    adi = round(adi, 3)
    cv2 = round(cv2, 3)

    # Cutoffs: ADI = 1.32, CV^2 = 0.49
    if adi < 1.32:
        if cv2 < 0.49:
            return "smooth", adi, cv2
        else:
            return "erratic", adi, cv2
    else:
        if cv2 < 0.49:
            return "intermittent", adi, cv2
        else:
            return "lumpy", adi, cv2


def generate_data_health_report(
    cleaned_df: pl.DataFrame,
    dataset_id: str,
    dataset_name: str,
    cleaning_meta: Dict[str, Any],
) -> DataHealthReport:
    """
    Analyzes the regularized dataset to produce the complete data-health report.
    """
    granularity = cleaning_meta.get("granularity", "daily")
    min_periods = (
        settings.MIN_SERIES_LENGTH_DAILY
        if granularity == "daily"
        else settings.MIN_SERIES_LENGTH_WEEKLY
    )
    inactive_days = settings.INACTIVE_THRESHOLD_DAYS

    # Overall dates
    min_date = cleaned_df["date"].min()
    max_date = cleaned_df["date"].max()
    total_days = (max_date - min_date).days + 1 if min_date and max_date else 0

    unique_products = cleaned_df["product"].unique().to_list()
    product_stats_list: List[ProductClassStats] = []
    class_distribution: Dict[str, int] = {
        "smooth": 0,
        "erratic": 0,
        "intermittent": 0,
        "lumpy": 0,
        "short_history": 0,
        "inactive": 0,
    }

    total_zero_demand_count = 0
    total_active_periods = 0

    for prod in unique_products:
        p_df = cleaned_df.filter(pl.col("product") == prod).sort("date")
        qty_arr = p_df["quantity"].to_numpy()
        dates_list = p_df["date"].to_list()

        # Find first and last positive sale date
        positive_dates = [d for d, q in zip(dates_list, qty_arr) if q > 0]
        first_sale = positive_dates[0] if positive_dates else dates_list[0]
        last_sale = positive_dates[-1] if positive_dates else dates_list[-1]

        series_class, adi, cv2 = calculate_syntetos_boylan(
            quantities=qty_arr,
            granularity=granularity,
            min_periods=min_periods,
            dataset_max_date=max_date,
            last_sale_date=last_sale,
            inactive_days=inactive_days,
        )

        class_distribution[series_class] = class_distribution.get(series_class, 0) + 1

        zero_count = int(np.sum(qty_arr == 0))
        total_zero_demand_count += zero_count
        total_active_periods += len(qty_arr)
        zero_ratio = round(zero_count / len(qty_arr), 3) if len(qty_arr) > 0 else 0.0

        outlier_count = int(p_df["is_outlier"].sum()) if "is_outlier" in p_df.columns else 0

        product_stats_list.append(
            ProductClassStats(
                product=str(prod),
                series_class=series_class,
                adi=adi,
                cv2=cv2,
                history_length_periods=len(qty_arr),
                first_sale_date=first_sale.isoformat() if first_sale else None,
                last_sale_date=last_sale.isoformat() if last_sale else None,
                total_sales_units=round(float(np.sum(qty_arr)), 2),
                active_days=(last_sale - first_sale).days + 1 if first_sale and last_sale else 0,
                zero_demand_ratio=zero_ratio,
                outlier_count=outlier_count,
            )
        )

    # Compile data quality issues with plain-English descriptions
    issues: List[DataQualityIssue] = []

    # 1. Duplicates
    dup_count = cleaning_meta.get("duplicate_count", 0)
    if dup_count > 0:
        issues.append(
            DataQualityIssue(
                issue_type="duplicates",
                severity="info",
                title=f"{dup_count} duplicate rows detected",
                description="We found multiple transaction records with identical details.",
                action_taken="Sales quantities were aggregated into the regular calendar grid.",
                affected_count=dup_count,
            )
        )

    # 2. Outliers
    outlier_count = cleaning_meta.get("outlier_count", 0)
    if outlier_count > 0:
        issues.append(
            DataQualityIssue(
                issue_type="outliers",
                severity="warning",
                title=f"{outlier_count} unusual sales spikes flagged",
                description="Sales quantities exceeded 3x the typical interquartile range for these items.",
                action_taken="Outliers are flagged in data and kept for baseline backtesting.",
                affected_count=outlier_count,
            )
        )

    # 3. Gaps / Zero Demand
    if total_zero_demand_count > 0:
        issues.append(
            DataQualityIssue(
                issue_type="gaps",
                severity="info",
                title=f"{total_zero_demand_count} zero-sale periods regularized",
                description="Days with no sales occurred between the product's first and last transaction.",
                action_taken="Filled with 0 units during the active lifecycle (no pre-launch zeros added).",
                affected_count=total_zero_demand_count,
            )
        )

    # 4. Inactive products
    inactive_count = class_distribution.get("inactive", 0)
    if inactive_count > 0:
        issues.append(
            DataQualityIssue(
                issue_type="inactive",
                severity="warning",
                title=f"{inactive_count} products have had no sales recently",
                description=f"No transactions recorded in the last {inactive_days} days.",
                action_taken="Categorized as 'inactive' (possibly discontinued); forecasts will be suppressed.",
                affected_count=inactive_count,
            )
        )

    # 5. Short history products
    short_count = class_distribution.get("short_history", 0)
    if short_count > 0:
        issues.append(
            DataQualityIssue(
                issue_type="short_history",
                severity="warning",
                title=f"{short_count} products have short sales history",
                description=f"Less than {min_periods} periods of observed data.",
                action_taken="Classified as 'short_history'; baseline forecasts will be labeled 'low confidence'.",
                affected_count=short_count,
            )
        )

    return DataHealthReport(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        total_rows_raw=cleaning_meta.get("raw_row_count", 0),
        total_rows_cleaned=cleaning_meta.get("cleaned_row_count", len(cleaned_df)),
        total_products=len(unique_products),
        date_min=min_date.isoformat() if min_date else None,
        date_max=max_date.isoformat() if max_date else None,
        total_days=total_days,
        granularity=granularity,
        class_distribution=class_distribution,
        issues=issues,
        products=product_stats_list,
    )
