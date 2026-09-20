from fastapi.testclient import TestClient
import pytest

from apps.api.app.main import app
from apps.api.app.db import init_db


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    init_db()


@pytest.fixture
def client():
    return TestClient(app)


def test_sample_dataset_loading_and_summary(client: TestClient):
    # 1. Trigger sample dataset load
    resp = client.post("/api/v1/datasets/sample")
    assert resp.status_code == 201
    data = resp.json()
    dataset_id = data["id"]
    assert data["name"] == "Demo Retail Store (40 Products)"
    assert data["status"] == "ready"
    assert data["product_count"] == 40

    # 2. Get health report
    h_resp = client.get(f"/api/v1/datasets/{dataset_id}/health")
    assert h_resp.status_code == 200
    h_data = h_resp.json()
    assert h_data["total_products"] == 40

    # 3. Test summary endpoint (with fallback template when Ollama is offline or fast test)
    s_resp = client.get(f"/api/v1/datasets/{dataset_id}/summary")
    assert s_resp.status_code == 200
    s_data = s_resp.json()
    assert "narrative" in s_data
    assert len(s_data["narrative"]) > 10
