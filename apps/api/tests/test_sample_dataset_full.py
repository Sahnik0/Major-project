import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app.db import init_db
from apps.api.app.main import app


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    init_db()


@pytest.fixture
def client():
    return TestClient(app)


def test_full_sample_dataset_pipeline(client: TestClient):
    csv_path = Path("data/sample_sales.csv")
    ground_truth_path = Path("data/sample_ground_truth.json")

    assert csv_path.exists(), (
        "Sample CSV does not exist. Run scripts/generate_sample_data.py first."
    )
    assert ground_truth_path.exists(), "Sample ground truth does not exist."

    with open(ground_truth_path, "r") as f:
        ground_truth = json.load(f)

    # 1. Upload sample file
    with open(csv_path, "rb") as f:
        upload_resp = client.post(
            "/api/v1/datasets",
            files={"file": ("sample_sales.csv", f, "text/csv")},
            data={"name": "Demo Synthetic Retail Data"},
        )
    assert upload_resp.status_code == 201
    upload_data = upload_resp.json()
    dataset_id = upload_data["id"]

    # Verify column suggestions
    suggested = upload_data["suggested_mapping"]
    assert suggested["date"] == "Date"
    assert suggested["product"] == "Product_SKU"
    assert suggested["quantity"] == "Units_Sold"
    assert suggested["revenue"] == "Revenue"

    # 2. Confirm mapping
    mapping_payload = {
        "mapping": {
            "date": "Date",
            "product": "Product_SKU",
            "quantity": "Units_Sold",
            "revenue": "Revenue",
            "store": "Store_ID",
            "promo_flag": "Promo_Flag",
        },
        "date_format": "iso",
        "drop_duplicates": False,
        "returns_handling": "net",
        "aggregation_granularity": "daily",
    }
    confirm_resp = client.put(f"/api/v1/datasets/{dataset_id}/mapping", json=mapping_payload)
    assert confirm_resp.status_code == 200
    confirm_data = confirm_resp.json()
    job_id = confirm_data["job_id"]

    # 3. Poll job progress until completed
    max_wait_seconds = 30
    start_time = time.time()
    job_completed = False

    while time.time() - start_time < max_wait_seconds:
        job_resp = client.get(f"/api/v1/jobs/{job_id}")
        assert job_resp.status_code == 200
        job_status = job_resp.json()
        if job_status["status"] == "completed":
            job_completed = True
            break
        elif job_status["status"] == "failed":
            pytest.fail(f"Job failed: {job_status.get('error')}")
        time.sleep(0.3)

    assert job_completed, f"Job did not finish in {max_wait_seconds}s"

    # 4. Fetch Health Report
    health_resp = client.get(f"/api/v1/datasets/{dataset_id}/health")
    assert health_resp.status_code == 200
    health_report = health_resp.json()

    assert health_report["total_products"] == 40
    assert health_report["total_rows_raw"] == ground_truth["total_rows"]
    assert health_report["class_distribution"]["short_history"] == 3
    assert health_report["class_distribution"]["inactive"] == 2

    # Verify each product classification against ground truth
    products_by_id = {p["product"]: p for p in health_report["products"]}
    for pid, expected_meta in ground_truth["products"].items():
        assert pid in products_by_id, f"Product {pid} missing from report"
        reported = products_by_id[pid]
        expected_class = expected_meta["expected_class"]
        assert reported["series_class"] == expected_class, (
            f"Product {pid} mismatch: expected {expected_class}, got {reported['series_class']} "
            f"(ADI: {reported['adi']}, CV2: {reported['cv2']})"
        )

    # Verify issues compilation
    issue_types = [i["issue_type"] for i in health_report["issues"]]
    assert "duplicates" in issue_types
    assert "outliers" in issue_types
    assert "gaps" in issue_types
    assert "inactive" in issue_types
    assert "short_history" in issue_types
