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


def test_health_endpoint(client: TestClient):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "device" in data
    assert "disk_free_gb" in data
    assert "timesfm_version" in data


def test_upload_mapping_and_health_flow(client: TestClient, tmp_path: Path):
    # 1. Create a mini sales CSV file
    csv_content = """Date,Product_SKU,Units_Sold,Revenue
2023-01-01,SKU_ALPHA,10,100.0
2023-01-02,SKU_ALPHA,12,120.0
2023-01-03,SKU_ALPHA,11,110.0
2023-01-01,SKU_BETA,5,50.0
2023-01-03,SKU_BETA,6,60.0
"""
    test_csv = tmp_path / "test_sales.csv"
    test_csv.write_text(csv_content)

    # 2. Upload file
    with open(test_csv, "rb") as f:
        upload_resp = client.post(
            "/api/v1/datasets",
            files={"file": ("test_sales.csv", f, "text/csv")},
            data={"name": "Test Dataset Flow"},
        )
    assert upload_resp.status_code == 201
    upload_data = upload_resp.json()
    dataset_id = upload_data["id"]
    assert upload_data["status"] == "mapping_pending"
    assert "Date" in upload_data["detected_columns"]
    assert upload_data["suggested_mapping"]["date"] == "Date"
    assert upload_data["suggested_mapping"]["product"] == "Product_SKU"
    assert upload_data["suggested_mapping"]["quantity"] == "Units_Sold"

    # 3. Confirm mapping
    mapping_payload = {
        "mapping": {
            "date": "Date",
            "product": "Product_SKU",
            "quantity": "Units_Sold",
            "revenue": "Revenue",
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
    assert confirm_data["status"] == "queued"

    # 4. Wait / poll job status
    import time

    for _ in range(20):
        job_resp = client.get(f"/api/v1/jobs/{job_id}")
        assert job_resp.status_code == 200
        job_data = job_resp.json()
        if job_data["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    assert job_data["status"] == "completed", f"Job failed with error: {job_data.get('error')}"
    assert job_data["progress"] == 100

    # 5. Fetch health report
    health_resp = client.get(f"/api/v1/datasets/{dataset_id}/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["total_products"] == 2
    assert "SKU_ALPHA" in [p["product"] for p in health_data["products"]]
    assert "SKU_BETA" in [p["product"] for p in health_data["products"]]
