"""Supervisor: turns a request into a validated plan, and writes the final summary.

Planning strategy
  * Bedrock mode: the LLM writes a structured Plan (Pydantic) from the tool catalog
    and also extracts structured inputs from free text.
  * The plan is VALIDATED (tools exist, each step's agent owns its tool, size limit).
  * If the LLM is unavailable or its plan is invalid, fall back to a known workflow template.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from app.agents.workflows import VENDOR_FIELDS, match_template
from app.config import Settings
from app.llm import chat_model, text_of
from app.models import Plan
from app.tools.base import catalog, get_tool


class PlanningError(Exception):
    pass


class LLMPlan(BaseModel):
    plan: Plan
    extracted_inputs: dict[str, Any] = Field(
        default_factory=dict, description="Structured values found in the request text"
    )


PLANNER_PROMPT = """You are the SUPERVISOR of a team of AI agents that automate business workflows.
Agents:
- research: gathers facts (read-only)
- analysis: queries data, computes, scores risk (read-only)
- execution: the ONLY agent that changes business systems

Write a plan as an ordered list of steps. Rules:
1. Use ONLY tools from the catalog, and assign each step to the agent that owns the tool.
2. Research and analysis come before any execution step.
3. For vendor/supplier work, always include company_registry_lookup, sanctions_screen and score_risk before execution.
4. Reference request inputs or earlier tool outputs with {{key}} (e.g. {{vendor_id}} after erp_create_vendor).
5. Keep it minimal: no step that the request does not need. At most %(max_steps)d steps.
6. Extract any structured values you find in the request text into extracted_inputs
   (for vendors use keys: %(vendor_fields)s).

Tool catalog:
%(catalog)s
"""


def validate_plan(plan: Plan, settings: Settings) -> None:
    if not plan.steps:
        raise PlanningError("Plan has no steps")
    if len(plan.steps) > settings.max_steps:
        raise PlanningError(f"Plan has {len(plan.steps)} steps (max {settings.max_steps})")
    ids = [s.id for s in plan.steps]
    if len(ids) != len(set(ids)):
        raise PlanningError("Duplicate step ids")
    seen_execution = False
    for s in plan.steps:
        try:
            tool = get_tool(s.action)
        except KeyError as e:
            raise PlanningError(str(e)) from e
        if tool.agent != s.agent:
            raise PlanningError(f"Step {s.id}: tool {s.action} belongs to {tool.agent}, not {s.agent}")
        if s.agent == "execution":
            seen_execution = True
        elif seen_execution:
            raise PlanningError(f"Step {s.id}: read steps must come before execution steps")


LLMCallback = Callable[[str, Any, int], None]   # (purpose, ai_message, latency_ms)
PLAN_ATTEMPTS = 2


def make_plan(request: str, inputs: dict, settings: Settings,
              on_llm: LLMCallback | None = None) -> tuple[Plan, dict, str]:
    """Returns (plan, extracted_inputs, planner_name).

    The LLM gets one chance to correct an invalid plan (the validation error is fed
    back to it). If it still fails, a known workflow template is used instead.
    """
    llm = chat_model("supervisor", settings)
    llm_error = None
    if llm is not None:
        planner = llm.with_structured_output(LLMPlan, include_raw=True)
        messages: list = [
            ("system", PLANNER_PROMPT % {
                "max_steps": settings.max_steps,
                "vendor_fields": ", ".join(VENDOR_FIELDS),
                "catalog": json.dumps(catalog(), indent=1),
            }),
            ("human", f"Request: {request}\nStructured inputs: {json.dumps(inputs)}"),
        ]
        for attempt in range(1, PLAN_ATTEMPTS + 1):
            try:
                t0 = time.perf_counter()
                out = planner.invoke(messages)
                if on_llm:
                    on_llm(f"plan_attempt_{attempt}", out.get("raw"), int((time.perf_counter() - t0) * 1000))
                if out.get("parsing_error") or out.get("parsed") is None:
                    raise PlanningError(f"plan could not be parsed: {out.get('parsing_error')}")
                parsed: LLMPlan = out["parsed"]
                validate_plan(parsed.plan, settings)
                name = "bedrock" if attempt == 1 else f"bedrock (self-corrected on attempt {attempt})"
                return parsed.plan, parsed.extracted_inputs, name
            except Exception as e:
                llm_error = f"{type(e).__name__}: {e}"
                messages.append(("human", f"Your plan was rejected: {llm_error}. "
                                          "Return a corrected plan that follows every rule."))

    plan = match_template(request)
    if plan is None:
        raise PlanningError(
            "No workflow template matches this request"
            + (f" and the LLM planner failed ({llm_error})" if llm_error
               else " (enable LLM_PROVIDER=bedrock for free-form planning)")
        )
    validate_plan(plan, settings)
    return plan, {}, "template" + (f" (LLM fallback: {llm_error})" if llm_error else "")


def write_summary(request: str, status: str, results: list[dict], facts: dict, settings: Settings,
                  halt_reason: str | None = None, on_llm: LLMCallback | None = None) -> str:
    lines = [f"- [{r['agent']}] {r['note']}" for r in results]
    llm = chat_model("supervisor", settings)
    if llm is not None:
        try:
            t0 = time.perf_counter()
            msg = llm.invoke([
                ("system", "Write a concise run summary for a business user (max 120 words). "
                           "State the outcome first, then key findings and any action they must take."),
                ("human", f"Request: {request}\nStatus: {status}\nStop reason: {halt_reason or '-'}\n"
                          f"Step results:\n" + "\n".join(lines)),
            ])
            if on_llm:
                on_llm("summary", msg, int((time.perf_counter() - t0) * 1000))
            return text_of(msg)
        except Exception:
            pass
    head = {"completed": "Completed.", "halted": f"Stopped: {halt_reason}.", "failed": f"Failed: {halt_reason}."}
    return head.get(status, status) + "\n" + "\n".join(lines)
