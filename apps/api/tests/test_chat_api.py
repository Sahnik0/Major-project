import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_chat_messages_flow():
    # 1. Load sample dataset
    resp = client.post("/api/v1/datasets/sample")
    assert resp.status_code == 201
    data = resp.json()
    dataset_id = data["id"]

    # 2. Get initial chat messages
    chat_resp = client.get(f"/api/v1/datasets/{dataset_id}/chat/messages")
    assert chat_resp.status_code == 200
    msgs = chat_resp.json()
    assert len(msgs) >= 1
    assert msgs[0]["card_type"] == "health_summary"

    # 3. Post a user question
    post_resp = client.post(
        f"/api/v1/datasets/{dataset_id}/chat",
        json={"message": "What should I reorder this week?"},
    )
    assert post_resp.status_code == 200
    reply = post_resp.json()
    assert reply["role"] == "assistant"
    assert "content" in reply
    assert len(reply["content"]) > 0

    # 4. Post product specific question
    prod_resp = client.post(
        f"/api/v1/datasets/{dataset_id}/chat",
        json={"message": "Tell me about PROD_01"},
    )
    assert prod_resp.status_code == 200
    prod_reply = prod_resp.json()
    assert prod_reply["role"] == "assistant"
    assert "PROD_01" in prod_reply["content"] or prod_reply["card_type"] == "product_focus"
