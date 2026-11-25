import json
from unittest.mock import MagicMock

import httpx
from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_chat_fails_and_saves_nothing_when_ollama_unavailable(monkeypatch):
    # Force Ollama connection error regardless of whether local Ollama is running
    async def fail_post(self, url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)

    # 1. Load sample dataset
    resp = client.post("/api/v1/datasets/sample")
    assert resp.status_code == 201
    dataset_id = resp.json()["id"]

    # 2. Get initial chat messages count
    before_msgs = client.get(f"/api/v1/datasets/{dataset_id}/chat/messages").json()
    before_count = len(before_msgs)
    assert before_count >= 1
    assert before_msgs[0]["card_type"] == "health_summary"

    # 3. Post a user question while Ollama is offline -> must return 503 and save NOTHING
    post_resp = client.post(
        f"/api/v1/datasets/{dataset_id}/chat",
        json={"message": "What is the total Units_Sold?"},
    )
    assert post_resp.status_code == 503
    err_body = post_resp.json()
    assert err_body["code"] == "OLLAMA_UNAVAILABLE"

    # 4. Verify neither the failed user prompt nor any fallback reply was persisted
    after_msgs = client.get(f"/api/v1/datasets/{dataset_id}/chat/messages").json()
    assert len(after_msgs) == before_count
    client.delete(f"/api/v1/datasets/{dataset_id}")


def test_chat_messages_flow(monkeypatch):
    # Mock Ollama to return deterministic SQL tool plan and verified synthesis
    async def fake_post(self, url, **kwargs):
        payload = kwargs.get("json") or {}
        prompt = payload.get("prompt", "")
        sys_p = payload.get("system", "")
        resp = MagicMock()
        resp.status_code = 200
        if "Generate Chain-of-Thought JSON plan" in prompt or "Select tool:" in prompt:
            if "PROD_01" in prompt:
                body = {
                    "reasoning_steps": ["Step 1: Filter dataset for PROD_01", "Step 2: Sum Units_Sold"],
                    "tool": "run_sql_query",
                    "arguments": {
                        "sql": 'SELECT "Product_SKU", SUM("Units_Sold") AS total_units FROM data WHERE "Product_SKU" = \'PROD_01\' GROUP BY "Product_SKU"',
                        "title": "PROD_01 Total Units",
                    },
                    "secondary_sql": None,
                }
            else:
                body = {
                    "reasoning_steps": ["Step 1: Inspect total Units_Sold across dataset"],
                    "tool": "run_sql_query",
                    "arguments": {
                        "sql": 'SELECT COUNT(*) AS records, SUM("Units_Sold") AS total_units FROM data',
                        "title": "Total Units_Sold",
                    },
                    "secondary_sql": None,
                }
            resp.json.return_value = {"response": json.dumps(body)}
        elif "answer_markdown" in sys_p:
            # Extract first fact number cleanly by echoing verified summary
            facts_line = prompt.split("Question:", 1)[0]
            nums = [w for w in facts_line.replace(",", "").split() if w.replace(".", "", 1).isdigit()]
            cited = nums[0] if nums else ""
            ans = f"Calculated directly from the full dataset ({cited})." if cited else "Calculated directly from the full dataset."
            resp.json.return_value = {
                "response": json.dumps({"synthesis_thought": "Verified from Facts", "answer_markdown": ans})
            }
        else:
            resp.json.return_value = {"response": "Calculated directly from the full dataset."}
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    # 1. Load sample dataset
    resp = client.post("/api/v1/datasets/sample")
    assert resp.status_code == 201
    dataset_id = resp.json()["id"]

    # 2. Post product specific question
    prod_resp = client.post(
        f"/api/v1/datasets/{dataset_id}/chat",
        json={"message": "What is the total Units_Sold for PROD_01?"},
    )
    assert prod_resp.status_code == 200
    prod_reply = prod_resp.json()
    assert prod_reply["role"] == "assistant"
    assert prod_reply["card_type"] == "tool_result"
    assert "PROD_01" in json.dumps(prod_reply["payload"])
    client.delete(f"/api/v1/datasets/{dataset_id}")
