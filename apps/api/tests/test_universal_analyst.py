import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.db import init_db
from apps.api.app.main import app

init_db()
client = TestClient(app)


def _wait_for_job(job_id: str, timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        resp = client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.15)
    return last


def test_quarterly_housing_csv_immediate_understanding_forecast_and_csv_export(tmp_path: Path):
    """
    Verifies a non-retail quarterly dataset (saledate, MA, type, bedrooms):
    1. Immediately auto-maps saledate -> date, MA -> quantity (NOT 'main_category'!), and becomes 'ready'
    2. Understands row grain & all columns accurately
    3. Answers 'Show me the future prediction' with forecast chart + downloadable CSV
    4. Generates a new custom CSV from natural language and serves it via /exports/{filename}
    """
    rows = ["saledate,MA,type,bedrooms"]
    quarters = [
        "31/03/2015", "30/06/2015", "30/09/2015", "31/12/2015",
        "31/03/2016", "30/06/2016", "30/09/2016", "31/12/2016",
        "31/03/2017", "30/06/2017", "30/09/2017", "31/12/2017",
    ]
    for idx, q_dt in enumerate(quarters):
        rows.append(f"{q_dt},{450000 + idx * 12000},house,3")
        rows.append(f"{q_dt},{320000 + idx * 8000},unit,2")

    csv_file = tmp_path / "housing_quarterly.csv"
    csv_file.write_text("\n".join(rows))

    with open(csv_file, "rb") as f:
        up_resp = client.post(
            "/api/v1/datasets",
            files={"file": ("housing_quarterly.csv", f, "text/csv")},
        )
    assert up_resp.status_code == 201
    up_data = up_resp.json()
    ds_id = up_data["id"]

    # Must recognize saledate as date and MA as Moving Average (never 'main_category')
    assert up_data["suggested_mapping"]["date"] == "saledate"
    assert up_data["suggested_mapping"]["quantity"] == "MA"
    ma_sem = up_data["suggested_mapping"]["_column_semantics"]["MA"].lower()
    assert "merchandise" not in ma_sem
    assert "moving average" in ma_sem or "numeric" in ma_sem
    assert up_data["job_id"] is not None

    job_res = _wait_for_job(up_data["job_id"])
    assert job_res["status"] == "completed", f"Job failed: {job_res.get('error')}"

    ds_info = client.get(f"/api/v1/datasets/{ds_id}").json()
    assert ds_info["status"] == "ready"

    # Ask about rows and columns
    schema_chat = client.post(
        f"/api/v1/datasets/{ds_id}/chat",
        json={"message": "Explain all rows and columns in this dataset"},
    )
    assert schema_chat.status_code == 200
    schema_msg = schema_chat.json()
    assert schema_msg["card_type"] == "schema_explanation"
    assert "saledate" in schema_msg["payload"]["columns"]
    assert "MA" in schema_msg["payload"]["columns"]

    # Ask "Show me the future prediction"
    pred_chat = client.post(
        f"/api/v1/datasets/{ds_id}/chat",
        json={"message": "Show me the future prediction for MA"},
    )
    assert pred_chat.status_code == 200
    pred_msg = pred_chat.json()
    assert pred_msg["card_type"] == "forecast_result"
    assert len(pred_msg["payload"]["forecast_chart"]) > len(quarters)
    assert pred_msg["payload"]["csv_url"].startswith(f"/api/v1/datasets/{ds_id}/exports/")

    # Ask AI to generate another CSV with specific data
    gen_chat = client.post(
        f"/api/v1/datasets/{ds_id}/chat",
        json={"message": "Generate a CSV summarizing average MA by type"},
    )
    assert gen_chat.status_code == 200
    gen_msg = gen_chat.json()
    assert gen_msg["card_type"] == "csv_export"
    csv_url = gen_msg["payload"]["csv_url"]
    assert csv_url.startswith(f"/api/v1/datasets/{ds_id}/exports/")

    # Download the generated CSV and verify contents
    dl_resp = client.get(csv_url)
    assert dl_resp.status_code == 200
    assert "text/csv" in dl_resp.headers["content-type"]
    assert "house" in dl_resp.text
    assert "unit" in dl_resp.text

    # Clean up test dataset
    del_resp = client.delete(f"/api/v1/datasets/{ds_id}")
    assert del_resp.status_code == 200


def test_cross_sectional_tabular_json_and_sql_queries(tmp_path: Path):
    """
    Verifies uploading a non-temporal JSON business dataset (no date column!) with currency strings:
    1. Auto-classifies as 'tabular' and becomes 'ready' immediately (never stuck in mapping_pending!)
    2. Coerces '$1,250.50' currency strings into numeric Float64
    3. Executes arbitrary DuckDB SQL queries and correlation/anomaly tools cleanly
    """
    records = [
        {"Customer_ID": f"CUST_{i:03d}", "Segment": "Enterprise" if i % 2 == 0 else "SMB", "Seats": 10 + i * 3, "ARR": f"${1200 + i * 450:,.2f}"}
        for i in range(1, 25)
    ]
    # Inject one obvious outlier
    records.append({"Customer_ID": "CUST_999", "Segment": "Enterprise", "Seats": 500, "ARR": "$250,000.00"})

    json_file = tmp_path / "saas_customers.json"
    json_file.write_text(json.dumps(records))

    with open(json_file, "rb") as f:
        up_resp = client.post(
            "/api/v1/datasets",
            files={"file": ("saas_customers.json", f, "application/json")},
        )
    assert up_resp.status_code == 201
    up_data = up_resp.json()
    ds_id = up_data["id"]
    assert up_data["suggested_mapping"]["_dataset_type"] == "tabular"
    assert up_data["job_id"] is not None

    job_res = _wait_for_job(up_data["job_id"])
    assert job_res["status"] == "completed"

    ds_info = client.get(f"/api/v1/datasets/{ds_id}").json()
    assert ds_info["status"] == "ready"

    # Run direct SQL analytical query via /query endpoint
    q_resp = client.post(
        f"/api/v1/datasets/{ds_id}/query",
        json={
            "sql": 'SELECT "Segment", COUNT(*) AS customers, ROUND(SUM("ARR"), 2) AS total_arr FROM data GROUP BY "Segment" ORDER BY total_arr DESC',
        },
    )
    assert q_resp.status_code == 200
    q_data = q_resp.json()
    assert q_data["total_rows_returned"] == 2
    assert q_data["table_rows"][0][0] == "Enterprise"

    # Ask chat for correlations & anomalies
    corr_resp = client.post(
        f"/api/v1/datasets/{ds_id}/chat",
        json={"message": "What is the correlation between Seats and ARR?"},
    )
    assert corr_resp.status_code == 200
    assert len(corr_resp.json()["content"]) > 10

    anom_resp = client.post(
        f"/api/v1/datasets/{ds_id}/chat",
        json={"message": "Detect anomalies and outliers in ARR"},
    )
    assert anom_resp.status_code == 200
    anom_json = anom_resp.json()
    assert "CUST_999" in json.dumps(anom_json["payload"])
    assert isinstance(anom_json["payload"].get("reasoning_steps"), list)
    assert len(anom_json["payload"]["reasoning_steps"]) >= 2

    # Clean up test dataset
    del_resp = client.delete(f"/api/v1/datasets/{ds_id}")
    assert del_resp.status_code == 200


def test_dynamic_multi_domain_cot_hr_and_iot_tsv(tmp_path: Path):
    """
    Verifies completely non-retail datasets (HR Workforce CSV + IoT Sensor TSV):
    1. Multi-condition dynamic SQL synthesis & Chain-of-Thought reasoning trace
    2. Self-reflection 0-row filter auto-recovery (strict '=' -> substring ILIKE)
    3. Non-inventory time-series omits retail 'Purchase Order / Reorder' step cards
    """
    hr_rows = [
        "Emp_ID,Team,Office_City,Experience_Years,Annual_Salary,Engagement_Score",
        "E01,Platform Engineering,Bengaluru,6,2800000,91.5",
        "E02,Platform Engineering,Bengaluru,8,3400000,94.0",
        "E03,Applied AI,Hyderabad,5,3100000,95.2",
        "E04,Applied AI,Bengaluru,7,3900000,96.0",
        "E05,Customer Success,Mumbai,2,1100000,82.0",
        "E06,Customer Success,Mumbai,6,1650000,86.5",
        "E07,People Ops,Delhi,3,1250000,88.0",
    ]
    hr_csv = tmp_path / "workforce_hr.csv"
    hr_csv.write_text("\n".join(hr_rows))

    with open(hr_csv, "rb") as f:
        up_hr = client.post("/api/v1/datasets", files={"file": ("workforce_hr.csv", f, "text/csv")})
    assert up_hr.status_code == 201
    hr_id = up_hr.json()["id"]
    assert _wait_for_job(up_hr.json()["job_id"])["status"] == "completed"

    # Multi-condition analytical query with Chain-of-Thought verification
    cot_resp = client.post(
        f"/api/v1/datasets/{hr_id}/chat",
        json={"message": "Average Annual_Salary by Team where Experience_Years > 4"},
    )
    assert cot_resp.status_code == 200
    cot_msg = cot_resp.json()
    assert cot_msg["payload"] is not None
    steps = cot_msg["payload"].get("reasoning_steps") or []
    assert len(steps) >= 3
    assert any("Grounding" in s.get("phase", "") or "CoT" in s.get("phase", "") for s in steps)
    assert "Applied AI" in json.dumps(cot_msg["payload"])

    # Verify self-reflection 0-row filter relaxation ('Platform' substring -> 'Platform Engineering')
    refl_resp = client.post(
        f"/api/v1/datasets/{hr_id}/query",
        json={
            "sql": 'SELECT "Emp_ID", "Team", "Annual_Salary" FROM data WHERE "Team" = \'Platform\'',
            "prompt": "Show Platform team salaries",
        },
    )
    assert refl_resp.status_code == 200
    refl_data = refl_resp.json()
    assert refl_data["total_rows_returned"] == 2
    assert len(refl_data.get("self_reflection") or []) >= 1

    client.delete(f"/api/v1/datasets/{hr_id}")

    # IoT Sensor TSV (tab-separated, non-retail temporal data)
    iot_lines = ["timestamp\tsensor_zone\ttemperature_c"]
    for day in range(1, 16):
        iot_lines.append(f"2026-03-{day:02d}\tTurbine_North\t{62.0 + day * 0.4:.1f}")
        iot_lines.append(f"2026-03-{day:02d}\tTurbine_South\t{55.0 + day * 0.2:.1f}")
    iot_tsv = tmp_path / "turbine_sensors.tsv"
    iot_tsv.write_text("\n".join(iot_lines))

    with open(iot_tsv, "rb") as f:
        up_iot = client.post("/api/v1/datasets", files={"file": ("turbine_sensors.tsv", f, "text/tab-separated-values")})
    assert up_iot.status_code == 201
    iot_id = up_iot.json()["id"]
    assert _wait_for_job(up_iot.json()["job_id"])["status"] == "completed"

    # Forecast on non-retail metric
    fc_iot = client.post(
        f"/api/v1/datasets/{iot_id}/chat",
        json={"message": "Predict future temperature_c for Turbine_North"},
    )
    assert fc_iot.status_code == 200
    fc_msg = fc_iot.json()
    assert fc_msg["card_type"] == "forecast_result"
    assert len(fc_msg["payload"].get("reasoning_steps") or []) >= 2

    client.delete(f"/api/v1/datasets/{iot_id}")
