import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

# Seed for reproducibility
np.random.seed(42)
random.seed(42)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_PATH = OUTPUT_DIR / "sample_sales.csv"
GROUND_TRUTH_PATH = OUTPUT_DIR / "sample_ground_truth.json"


def generate_dataset():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    start_date = date(2022, 1, 1)
    end_date = date(2023, 12, 31)
    total_days = (end_date - start_date).days + 1
    all_dates = [start_date + timedelta(days=i) for i in range(total_days)]

    # 40 Products:
    # 01-10: Smooth
    # 11-20: Erratic
    # 21-30: Intermittent
    # 31-35: Lumpy
    # 36-38: Short history (introduced in last 45 days)
    # 39-40: Inactive / Discontinued (sales stopped >75 days before end)
    products_meta: Dict[str, Dict[str, Any]] = {}

    for i in range(1, 11):
        pid = f"PROD_{i:02d}"
        products_meta[pid] = {
            "expected_class": "smooth",
            "base_demand": random.uniform(25, 60),
            "weekly_seasonality": True,
            "trend": random.uniform(0.01, 0.03),
            "active_start": start_date,
            "active_end": end_date,
        }

    for i in range(11, 21):
        pid = f"PROD_{i:02d}"
        products_meta[pid] = {
            "expected_class": "erratic",
            "base_demand": random.uniform(20, 50),
            "weekly_seasonality": True,
            "trend": 0.0,
            "active_start": start_date,
            "active_end": end_date,
        }

    for i in range(21, 31):
        pid = f"PROD_{i:02d}"
        products_meta[pid] = {
            "expected_class": "intermittent",
            "base_demand": random.uniform(5, 12),
            "prob_sale": 0.25,  # ~1 sale every 4 days -> ADI ~ 4.0
            "active_start": start_date,
            "active_end": end_date,
        }

    for i in range(31, 36):
        pid = f"PROD_{i:02d}"
        products_meta[pid] = {
            "expected_class": "lumpy",
            "base_demand": random.uniform(10, 40),
            "prob_sale": 0.20,  # ADI >= 1.32 & high variance
            "active_start": start_date,
            "active_end": end_date,
        }

    # Short history products (started Nov 20, 2023 -> ~41 days)
    for i in range(36, 39):
        pid = f"PROD_{i:02d}"
        products_meta[pid] = {
            "expected_class": "short_history",
            "base_demand": random.uniform(15, 30),
            "active_start": date(2023, 11, 20),
            "active_end": end_date,
        }

    # Discontinued products (ended Sept 15, 2023 -> >100 days inactive)
    for i in range(39, 41):
        pid = f"PROD_{i:02d}"
        products_meta[pid] = {
            "expected_class": "inactive",
            "base_demand": random.uniform(20, 35),
            "active_start": start_date,
            "active_end": date(2023, 9, 15),
        }

    # Festival Spikes:
    # Diwali 2022: Oct 24, Diwali 2023: Nov 12
    # Christmas 2022 & 2023: Dec 25
    festival_dates = {
        date(2022, 10, 24): "Diwali 2022",
        date(2023, 11, 12): "Diwali 2023",
        date(2022, 12, 25): "Christmas 2022",
        date(2023, 12, 25): "Christmas 2023",
    }

    # Generate records
    rows: List[Dict[str, Any]] = []
    injected_outliers = 0
    injected_returns = 0

    for current_date in all_dates:
        day_of_year = current_date.timetuple().tm_yday
        day_of_week = current_date.weekday()  # 0=Mon, 6=Sun
        is_weekend = day_of_week in (5, 6)

        # Global festival multiplier
        is_festival = current_date in festival_dates

        for pid, meta in products_meta.items():
            if current_date < meta["active_start"] or current_date > meta["active_end"]:
                continue

            exp_class = meta["expected_class"]
            qty = 0

            # Random promo flag
            promo_flag = 1 if random.random() < 0.08 else 0

            if exp_class == "smooth":
                # Daily sales with low variance: CV^2 < 0.49
                # Weekend lift + slight annual seasonality
                weekend_mult = 1.3 if is_weekend else 1.0
                season_mult = 1.0 + 0.15 * np.sin(2 * np.pi * day_of_year / 365)
                fest_mult = 2.2 if is_festival else 1.0
                promo_mult = 1.6 if promo_flag else 1.0
                mean_qty = meta["base_demand"] * weekend_mult * season_mult * fest_mult * promo_mult
                # Low standard deviation (std ~ 0.2 * mean -> CV ~ 0.2 -> CV^2 ~ 0.04)
                qty = max(1, int(np.random.normal(mean_qty, 0.18 * mean_qty)))

            elif exp_class == "erratic":
                # Regular daily sales, but high variance (CV^2 >= 0.49)
                # Lognormal or high-std normal
                weekend_mult = 1.2 if is_weekend else 1.0
                fest_mult = 1.8 if is_festival else 1.0
                mean_val = meta["base_demand"] * weekend_mult * fest_mult
                # Large variance: draw from lognormal
                sigma = 0.8  # CV ~ sqrt(exp(0.64)-1) ~ 0.95 -> CV^2 ~ 0.9
                qty = max(
                    1,
                    int(np.random.lognormal(mean=np.log(mean_val) - 0.5 * (sigma**2), sigma=sigma)),
                )

            elif exp_class == "intermittent":
                # Infrequent demand (ADI >= 1.32), steady order size (CV^2 < 0.49)
                if random.random() < meta["prob_sale"]:
                    qty = max(1, int(np.random.normal(meta["base_demand"], 1.5)))
                else:
                    qty = 0

            elif exp_class == "lumpy":
                # Infrequent demand AND high variance (ADI >= 1.32, CV^2 >= 0.49)
                if random.random() < meta["prob_sale"]:
                    sigma = 0.85
                    qty = max(
                        1, int(np.random.lognormal(mean=np.log(meta["base_demand"]), sigma=sigma))
                    )
                else:
                    qty = 0

            elif exp_class in ("short_history", "inactive"):
                # Active periods behavior
                qty = max(1, int(np.random.normal(meta["base_demand"], 4.0)))

            # If qty == 0, omit the row for sparse items (simulating real transactions where 0s are not logged)
            if qty <= 0:
                continue

            # Inject a few extreme outliers (10x spike)
            if random.random() < 0.001 and injected_outliers < 15:
                qty = int(qty * 9)
                injected_outliers += 1

            # Inject small returns (negative quantity)
            if random.random() < 0.008 and injected_returns < 25:
                qty = -int(random.randint(1, 5))
                injected_returns += 1

            unit_price = round(random.uniform(15.0, 150.0), 2)
            revenue = round(qty * unit_price, 2)
            store_id = f"STORE_{random.randint(1, 3):02d}"

            rows.append(
                {
                    "Date": current_date.isoformat(),
                    "Product_SKU": pid,
                    "Units_Sold": qty,
                    "Revenue": revenue,
                    "Store_ID": store_id,
                    "Promo_Flag": promo_flag,
                }
            )

    # Inject exact duplicates
    duplicates_to_add = random.sample(rows, min(30, len(rows)))
    rows.extend(duplicates_to_add)

    # Shuffle slightly
    random.shuffle(rows)

    # Write CSV
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Date", "Product_SKU", "Units_Sold", "Revenue", "Store_ID", "Promo_Flag"],
        )
        writer.writeheader()
        writer.writerows(rows)

    # Ground truth summary
    ground_truth = {
        "total_rows": len(rows),
        "injected_duplicates": len(duplicates_to_add),
        "injected_outliers": injected_outliers,
        "injected_returns": injected_returns,
        "products": {
            pid: {
                "expected_class": meta["expected_class"],
                "active_start": meta["active_start"].isoformat(),
                "active_end": meta["active_end"].isoformat(),
            }
            for pid, meta in products_meta.items()
        },
    }

    with open(GROUND_TRUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)

    print(f"Generated {len(rows)} rows across {len(products_meta)} products.")
    print(f"CSV written to: {CSV_PATH}")
    print(f"Ground truth written to: {GROUND_TRUTH_PATH}")


if __name__ == "__main__":
    generate_dataset()
