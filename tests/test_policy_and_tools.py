import pytest
from pydantic import ValidationError

import app.tools  # noqa: F401
from app import policy
from app.agents.common import MissingValue, resolve
from app.agents.supervisor import PlanningError, validate_plan
from app.agents.workflows import vendor_onboarding
from app.models import Plan, PlanStep
from app.tools.base import ToolContext, get_tool
from app.tools.mock_systems import MockSystems


def test_bank_details_always_need_approval(settings):
    d = policy.evaluate(get_tool("erp_set_bank_details"), {}, settings)
    assert d.requires_approval and d.tier == "high"


def test_payment_threshold(settings):
    tool = get_tool("issue_payment")
    assert not policy.evaluate(tool, {"amount": 500}, settings).requires_approval
    assert policy.evaluate(tool, {"amount": 5000}, settings).requires_approval


def test_strict_mode_gates_medium_actions(settings, monkeypatch):
    from dataclasses import replace
    strict = replace(settings, approval_mode="strict")
    tool = get_tool("send_email")
    assert policy.evaluate(tool, {"to": "x@vendor.com"}, strict).requires_approval
    assert not policy.evaluate(tool, {"to": "x@example.com"}, strict).requires_approval  # internal


def test_read_tools_never_need_approval(settings):
    assert not policy.evaluate(get_tool("sanctions_screen"), {}, settings).requires_approval


def test_resolve_templates():
    facts = {"vendor_id": "V-1", "amount": 10.5}
    assert resolve({"a": "{{vendor_id}}", "b": "Vendor {{vendor_id}}", "c": "{{amount}}"}, facts) == \
        {"a": "V-1", "b": "Vendor V-1", "c": 10.5}
    with pytest.raises(MissingValue):
        resolve("{{nope}}", facts)


def test_tool_inputs_are_validated(settings):
    ctx = ToolContext(settings, MockSystems(settings.systems_db), "r1")
    with pytest.raises(ValidationError):
        get_tool("erp_set_bank_details").run(ctx, {"vendor_id": "V-1", "iban": "short", "account_name": "x"})
    with pytest.raises(ValidationError):
        get_tool("send_email").run(ctx, {"to": "not-an-email", "subject": "s", "body": "b"})


def test_plan_validation_enforces_least_privilege(settings):
    validate_plan(vendor_onboarding(), settings)
    bad = Plan(goal="x", steps=[PlanStep(id="s1", agent="research", action="erp_set_bank_details",
                                         description="research agent trying to write")])
    with pytest.raises(PlanningError):
        validate_plan(bad, settings)
    out_of_order = Plan(goal="x", steps=[
        PlanStep(id="s1", agent="execution", action="slack_notify", args={"text": "hi"}, description="a"),
        PlanStep(id="s2", agent="research", action="news_search", args={"vendor_name": "x"}, description="b")])
    with pytest.raises(PlanningError):
        validate_plan(out_of_order, settings)
