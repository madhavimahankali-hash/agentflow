from fastapi.testclient import TestClient

from app import api
from tests.conftest import VENDOR_OK


def test_api_end_to_end(runner):
    api.set_runner(runner)
    client = TestClient(api.app)

    r = client.post("/runs", json={"request": "Onboard FastFreight LLC as a vendor", "inputs": VENDOR_OK})
    assert r.status_code == 202
    run_id = r.json()["run_id"]  # TestClient runs background tasks before returning

    assert client.get(f"/runs/{run_id}").json()["status"] == "awaiting_approval"
    pending = client.get("/approvals").json()
    assert len(pending) == 1 and pending[0]["tool"] == "erp_set_bank_details"

    d = client.post(f"/approvals/{pending[0]['id']}", json={"decision": "approve", "approver": "cfo@example.com"})
    assert d.status_code == 200
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"

    again = client.post(f"/approvals/{pending[0]['id']}", json={"decision": "reject"})
    assert again.status_code == 409

    hist = client.get(f"/runs/{run_id}/history").json()
    assert hist["approvals"][0]["status"] == "approved"
    assert any(e["type"] == "run_finished" for e in hist["events"])

    assert client.get("/systems").json()["erp_vendors"][0]["status"] == "active"
    assert len(client.get("/tools").json()) >= 10
    assert "supervisor" in client.get("/graph").json()["mermaid"]
    assert client.get("/").status_code == 200


def test_unknown_run_is_404(runner):
    api.set_runner(runner)
    assert TestClient(api.app).get("/runs/nope").status_code == 404
