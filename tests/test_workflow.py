"""End-to-end behaviour of the multi-agent graph (offline mode)."""
from app.runner import ApprovalConflict, Runner
from tests.conftest import VENDOR_BAD, VENDOR_OK


def tools_called(runner, run_id):
    return [e["data"]["tool"] for e in runner.history.events(run_id) if e["type"] == "tool_call"]


def test_low_risk_vendor_pauses_only_for_bank_details(runner):
    run_id = runner.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    state = runner.state(run_id)
    assert runner.history.get_run(run_id)["status"] == "awaiting_approval"
    assert state["pending"]["tool"] == "erp_set_bank_details"
    assert state["pending"]["tier"] == "high"
    # Nothing sensitive happened yet: vendor exists but has no bank details.
    vendor = runner.systems.snapshot()["erp_vendors"][0]
    assert vendor["status"] == "inactive" and vendor["iban"] is None
    # Supervisor delegated research -> analysis -> execution in order.
    agents = [r["agent"] for r in state["results"]]
    assert agents == ["research"] * 3 + ["analysis"] * 3 + ["execution"] * 2


def test_approve_resumes_and_completes(runner):
    run_id = runner.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    approval_id = runner.state(run_id)["pending"]["approval_id"]
    runner.decide(approval_id, "approve", "cfo@example.com", "verified by phone")
    assert runner.resume(run_id, "approve", "cfo@example.com", "verified by phone") == "completed"
    vendor = runner.systems.snapshot()["erp_vendors"][0]
    assert vendor["status"] == "active" and vendor["iban"] == "US12345678901234567890"
    assert runner.history.get_approval(approval_id)["decided_by"] == "cfo@example.com"
    assert tools_called(runner, run_id)[-1] == "slack_notify"


def test_reject_halts_without_writing(runner):
    run_id = runner.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    approval_id = runner.state(run_id)["pending"]["approval_id"]
    runner.decide(approval_id, "reject", "cfo@example.com", "IBAN does not match invoice")
    assert runner.resume(run_id, "reject", "cfo@example.com", "IBAN does not match invoice") == "halted"
    vendor = runner.systems.snapshot()["erp_vendors"][0]
    assert vendor["iban"] is None and vendor["status"] == "inactive"
    assert "erp_activate_vendor" not in tools_called(runner, run_id)


def test_edited_args_are_used_and_validated(runner):
    run_id = runner.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    pending = runner.state(run_id)["pending"]
    fixed = {**pending["args"], "iban": "US99 0000 1111 2222 3333 44"}
    runner.decide(pending["approval_id"], "approve", "cfo@example.com", "corrected IBAN", fixed)
    runner.resume(run_id, "approve", "cfo@example.com", "corrected IBAN", fixed)
    assert runner.systems.snapshot()["erp_vendors"][0]["iban"] == "US990000111122223333" + "44"


def test_double_decision_is_rejected(runner):
    run_id = runner.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    approval_id = runner.state(run_id)["pending"]["approval_id"]
    runner.decide(approval_id, "approve", "a@example.com")
    try:
        runner.decide(approval_id, "reject", "b@example.com")
        raise AssertionError("expected ApprovalConflict")
    except ApprovalConflict:
        pass


def test_high_risk_vendor_is_stopped_before_execution(runner):
    run_id = runner.start("Onboard supplier Shadow Cargo Ltd", VENDOR_BAD)
    state = runner.state(run_id)
    assert state["status"] == "halted"
    assert "sanctions match" in state["halt_reason"]
    assert runner.systems.snapshot()["erp_vendors"] == []
    assert not any(r["agent"] == "execution" for r in state["results"])


def test_second_workflow_reuses_the_same_agents(runner):
    run_id = runner.start("Send the weekly pipeline report", {"week": "2026-W45"})
    assert runner.state(run_id)["status"] == "completed"
    assert tools_called(runner, run_id) == ["sales_pipeline_summary", "slack_notify"]
    assert "won $85,000" in runner.systems.snapshot()["slack_log"][0]["text"]


def test_unknown_request_fails_cleanly(runner):
    run_id = runner.start("Book me a flight to Paris", {})
    assert runner.history.get_run(run_id)["status"] == "failed"


def test_missing_input_fails_cleanly(runner):
    inputs = {k: v for k, v in VENDOR_OK.items() if k != "contact_email"}
    run_id = runner.start("Onboard FastFreight LLC as a vendor", inputs)
    state = runner.state(run_id)
    assert state["status"] == "failed" and "contact_email" in state["halt_reason"]


def test_run_survives_restart(settings):
    """Approval can arrive after the process restarts: state comes from the checkpointer."""
    first = Runner(settings)
    run_id = first.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    approval_id = first.state(run_id)["pending"]["approval_id"]

    second = Runner(settings)  # simulates a new process
    second.decide(approval_id, "approve", "cfo@example.com")
    assert second.resume(run_id, "approve", "cfo@example.com") == "completed"


def test_execution_history_is_complete(runner):
    run_id = runner.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    types = [e["type"] for e in runner.history.events(run_id)]
    for t in ("run_started", "plan_created", "delegated", "tool_call", "step_completed", "approval_requested"):
        assert t in types
    tool_events = [e for e in runner.history.events(run_id) if e["type"] == "tool_call"]
    assert all("latency_ms" in e["data"] for e in tool_events)


def test_idempotent_replay_does_not_duplicate_writes(runner):
    run_id = runner.start("Onboard FastFreight LLC as a vendor", VENDOR_OK)
    # Pretend the step that created the vendor is replayed after a crash.
    cached = runner.history.get_idempotent(f"{run_id}:s7")
    assert cached and cached["vendor_id"].startswith("V-")
    assert len(runner.systems.snapshot()["erp_vendors"]) == 1
