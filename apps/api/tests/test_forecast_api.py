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


def test_end_to_end_forecast_and_inventory_api(client: TestClient, tmp_path: Path):
    # 1. Create and upload a dataset with 2 products
    from datetime import date, timedelta
    start = date(2023, 1, 1)
    rows = ["Date,Product_SKU,Units_Sold,Revenue"]
    for i in range(70):
        dt = (start + timedelta(days=i)).isoformat()
        rows.append(f"{dt},SKU_A,{10 + (i%7)},100.0")
        rows.append(f"{dt},SKU_B,{15 if i%4==0 else 0},50.0")

    csv_file = tmp_path / "forecast_test.csv"
    csv_file.write_text("\n".join(rows))

    with open(csv_file, "rb") as f:
        upload_resp = client.post(
            "/api/v1/datasets",
            files={"file": ("forecast_test.csv", f, "text/csv")},
            data={"name": "Forecast API Test Dataset"},
        )
    assert upload_resp.status_code == 201
    dataset_id = upload_resp.json()["id"]

    # 2. Confirm mapping
    confirm_resp = client.put(
        f"/api/v1/datasets/{dataset_id}/mapping",
        json={
            "mapping": {"date": "Date", "product": "Product_SKU", "quantity": "Units_Sold", "revenue": "Revenue"},
            "date_format": "iso",
            "aggregation_granularity": "daily",
        },
    )
    assert confirm_resp.status_code == 200
    cleaning_job_id = confirm_resp.json()["job_id"]

    # Wait for cleaning job
    for _ in range(20):
        job_data = client.get(f"/api/v1/jobs/{cleaning_job_id}").json()
        if job_data["status"] == "completed":
            break
        time.sleep(0.3)
    assert job_data["status"] == "completed"

    # 3. Trigger forecast job
    fc_resp = client.post(
        f"/api/v1/datasets/{dataset_id}/forecast-jobs",
        json={
            "horizon": 14,
            "num_folds": 2,
            "service_level": 0.90,
            "lead_time_days": 5,
            "review_period_days": 5,
        },
    )
    assert fc_resp.status_code == 202
    fc_job_id = fc_resp.json()["job_id"]

    # 4. Wait for forecast job
    for _ in range(40):
        job_data = client.get(f"/api/v1/jobs/{fc_job_id}").json()
        if job_data["status"] in ("completed", "failed"):
            break
        time.sleep(0.4)
    assert job_data["status"] == "completed", f"Job failed: {job_data.get('error')}"

    # 5. List products
    prods_resp = client.get(f"/api/v1/datasets/{dataset_id}/products")
    assert prods_resp.status_code == 200
    prods_data = prods_resp.json()
    assert prods_data["total"] == 2
    skus = [p["product"] for p in prods_data["products"]]
    assert "SKU_A" in skus
    assert "SKU_B" in skus

    # 6. Get product detail for SKU_A
    detail_resp = client.get(f"/api/v1/datasets/{dataset_id}/products/SKU_A")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["product"] == "SKU_A"
    assert len(detail_data["point_forecast"]) == 14
    assert "0.1" in detail_data["quantiles"] or 0.1 in detail_data["quantiles"]
    assert "0.9" in detail_data["quantiles"] or 0.9 in detail_data["quantiles"]
    assert len(detail_data["backtest_table"]) > 0
    assert detail_data["inventory_advice"]["reorder_point"] > 0

    # 7. Get inventory recommendations
    inv_resp = client.get(f"/api/v1/datasets/{dataset_id}/inventory")
    assert inv_resp.status_code == 200
    inv_data = inv_resp.json()
    assert inv_data["total_products"] == 2

    # 8. Export CSV
    csv_export = client.get(f"/api/v1/datasets/{dataset_id}/inventory.csv")
    assert csv_export.status_code == 200
    assert "text/csv" in csv_export.headers["content-type"]
    assert "product,series_class" in csv_export.text
    assert "SKU_A" in csv_export.text
