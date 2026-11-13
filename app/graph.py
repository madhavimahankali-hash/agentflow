"""The LangGraph workflow: supervisor + specialist agents + human approval gate.

    START -> supervisor -> {research | analysis | execution | finalize}
    research  -> supervisor
    analysis  -> supervisor
    execution -> {approval | supervisor}
    approval  -> execution            (pauses with interrupt() until a human decides)
    finalize  -> END

Agents never call each other: every hand-off goes through the supervisor and the
shared, typed state. The state is checkpointed after every node, so a run can wait
hours for an approval and survive a restart.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app import policy
from app.agents.common import MissingValue, call_tool, facts_from, resolve
from app.agents.supervisor import PlanningError, make_plan, write_summary
from app.agents.workers import run_read_agent
from app.config import Settings
from app.history import HistoryStore
from app.models import Plan, PlanStep
from app.tools.base import ToolContext, get_tool
from app.tools.mock_systems import MockSystems

EVIDENCE_KEYS = ("risk_level", "risk_score", "risk_reasons", "registry_status",
                 "sanctions_hits", "adverse_news", "rate_delta_pct", "budget_ok")


class RunState(TypedDict, total=False):
    run_id: str
    request: str
    inputs: dict
    plan: dict | None
    planner: str
    cursor: int
    step_count: int
    facts: dict            # request inputs + every tool output so far
    results: list[dict]    # one entry per finished step: {step_id, agent, action, note, status}
    pending: dict | None   # approval waiting for a human
    decision: dict | None  # the human's decision, consumed by the execution node
    status: str            # running | awaiting_approval | completed | halted | failed
    halt_reason: str | None
    summary: str
    next: str


def build_graph(settings: Settings, history: HistoryStore, systems: MockSystems) -> StateGraph:

    def current_step(state: RunState) -> PlanStep:
        return PlanStep.model_validate(state["plan"]["steps"][state["cursor"]])

    def ctx_for(state: RunState) -> ToolContext:
        return ToolContext(settings, systems, state["run_id"], dict(state.get("facts", {})))

    def stop(status: str, reason: str) -> dict:
        return {"status": status, "halt_reason": reason, "next": "finalize"}

    # ---------------- supervisor ----------------
    def supervisor(state: RunState) -> dict:
        run_id = state["run_id"]
        if state.get("status") in ("halted", "failed"):
            return {"next": "finalize"}

        update: dict[str, Any] = {}
        if not state.get("plan"):
            try:
                plan, extracted, planner = make_plan(state["request"], state.get("inputs", {}), settings)
            except PlanningError as e:
                history.record_event(run_id, "planning_failed", agent="supervisor", error=str(e))
                return stop("failed", str(e))
            facts = {**extracted, **state.get("inputs", {})}  # user-provided inputs win
            history.record_event(run_id, "plan_created", agent="supervisor", planner=planner,
                                 plan=plan.model_dump(), extracted_inputs=extracted)
            update = {"plan": plan.model_dump(), "planner": planner, "cursor": 0, "step_count": 0,
                      "facts": facts, "results": []}
            state = {**state, **update}

        plan = Plan.model_validate(state["plan"])
        cursor = state["cursor"]
        if state.get("step_count", 0) >= settings.max_steps:
            return {**update, **stop("failed", f"step limit {settings.max_steps} reached")}
        if cursor >= len(plan.steps):
            return {**update, "next": "finalize"}

        step = plan.steps[cursor]
        facts = state.get("facts", {})
        # Supervisor checkpoint: never let a high-risk vendor reach the execution agent.
        if step.agent == "execution" and facts.get("risk_level") == "high":
            reason = "risk is high (" + ", ".join(facts.get("risk_reasons", [])) + "); needs manual review"
            history.record_event(run_id, "run_halted", agent="supervisor", step_id=step.id, reason=reason)
            return {**update, **stop("halted", reason)}

        history.record_event(run_id, "delegated", agent="supervisor", step_id=step.id,
                             to=step.agent, action=step.action, description=step.description)
        return {**update, "status": "running", "next": step.agent}

    # ---------------- research / analysis ----------------
    def make_read_node(agent: str):
        def node(state: RunState) -> dict:
            step = current_step(state)
            ctx = ctx_for(state)
            try:
                new_facts, note = run_read_agent(agent, step, ctx, settings, history)
            except Exception as e:
                history.record_event(state["run_id"], "step_failed", agent=agent, step_id=step.id,
                                     error=f"{type(e).__name__}: {e}")
                return stop("failed", f"{agent} step {step.id} failed: {e}")
            history.record_event(state["run_id"], "step_completed", agent=agent, step_id=step.id, note=note)
            return {
                "facts": {**state.get("facts", {}), **new_facts},
                "results": state.get("results", []) + [
                    {"step_id": step.id, "agent": agent, "action": step.action, "note": note, "status": "done"}],
                "cursor": state["cursor"] + 1,
                "step_count": state.get("step_count", 0) + 1,
                "next": "supervisor",
            }
        return node

    # ---------------- execution ----------------
    def execution(state: RunState) -> dict:
        run_id, step = state["run_id"], current_step(state)
        tool = get_tool(step.action)
        if tool.agent != "execution":
            return stop("failed", f"execution agent may not use {tool.name}")
        try:
            args = resolve(step.args, state.get("facts", {}))
        except MissingValue as e:
            history.record_event(run_id, "step_failed", agent="execution", step_id=step.id,
                                 error=f"missing value {e}")
            return stop("failed", f"step {step.id} is missing input {e}")

        decision = state.get("decision")
        approved_by = None
        tier = None
        if decision and decision.get("step_id") == step.id:
            if decision["decision"] != "approve":
                reason = f"{tool.name} {decision['decision']} by {decision.get('approver')}: {decision.get('comment') or 'no comment'}"
                history.record_event(run_id, "run_halted", agent="execution", step_id=step.id, reason=reason)
                return {**stop("halted", reason), "decision": None}
            args = decision.get("edited_args") or args
            approved_by = decision.get("approver")
            tier = "high"
        else:
            pd = policy.evaluate(tool, args, settings)
            tier = pd.tier
            if pd.requires_approval:
                approval_id = f"{run_id}-{step.id}"
                facts = state.get("facts", {})
                evidence = {k: facts[k] for k in EVIDENCE_KEYS if k in facts}
                evidence["findings"] = [r["note"] for r in state.get("results", [])]
                if history.create_approval(approval_id, run_id, step.id, tool.name, args, pd.tier, pd.reason, evidence):
                    history.record_event(run_id, "approval_requested", agent="execution", step_id=step.id,
                                         approval_id=approval_id, tool=tool.name, tier=pd.tier, reason=pd.reason)
                pending = {"approval_id": approval_id, "step_id": step.id, "tool": tool.name, "args": args,
                           "tier": pd.tier, "reason": pd.reason, "evidence": evidence,
                           "description": step.description}
                return {"pending": pending, "status": "awaiting_approval", "next": "approval"}

        # Idempotency: a retried or resumed step never performs the same write twice.
        key = f"{run_id}:{step.id}"
        cached = history.get_idempotent(key)
        ctx = ctx_for(state)
        try:
            if cached is not None:
                result, ms = cached, 0
                history.record_event(run_id, "idempotent_replay", agent="execution", step_id=step.id, tool=tool.name)
            else:
                result, ms = call_tool(tool, ctx, args)
                history.save_idempotent(key, tool.name, result)
        except Exception as e:
            history.record_event(run_id, "step_failed", agent="execution", step_id=step.id, tool=tool.name,
                                 args=args, error=f"{type(e).__name__}: {e}")
            return {**stop("failed", f"{tool.name} failed: {e}"), "decision": None}

        history.record_event(run_id, "tool_call", agent="execution", step_id=step.id, tool=tool.name,
                             args=args, result=result, latency_ms=ms, tier=tier, approved_by=approved_by)
        history.record_event(run_id, "step_completed", agent="execution", step_id=step.id, note=result["summary"])
        return {
            "facts": {**state.get("facts", {}), **facts_from(result)},
            "results": state.get("results", []) + [
                {"step_id": step.id, "agent": "execution", "action": tool.name, "note": result["summary"],
                 "status": "done", "approved_by": approved_by}],
            "cursor": state["cursor"] + 1,
            "step_count": state.get("step_count", 0) + 1,
            "decision": None, "pending": None, "status": "running",
            "next": "supervisor",
        }

    # ---------------- human approval ----------------
    def approval(state: RunState) -> dict:
        # Pauses the graph here. The run resumes when the API calls Command(resume=decision).
        decision = interrupt(state["pending"])
        history.record_event(state["run_id"], "approval_decided", agent="human", step_id=state["pending"]["step_id"],
                             approval_id=state["pending"]["approval_id"], decision=decision.get("decision"),
                             approver=decision.get("approver"), comment=decision.get("comment"))
        return {"decision": {**decision, "step_id": state["pending"]["step_id"]},
                "pending": None, "status": "running", "next": "execution"}

    # ---------------- finalize ----------------
    def finalize(state: RunState) -> dict:
        status = state.get("status", "running")
        if status in ("running", "awaiting_approval"):
            status = "completed"
        summary = write_summary(state["request"], status, state.get("results", []), state.get("facts", {}),
                                settings, state.get("halt_reason"))
        history.update_run(state["run_id"], status, summary)
        history.record_event(state["run_id"], "run_finished", agent="supervisor", status=status, summary=summary)
        return {"status": status, "summary": summary}

    g = StateGraph(RunState)
    g.add_node("supervisor", supervisor)
    g.add_node("research", make_read_node("research"))
    g.add_node("analysis", make_read_node("analysis"))
    g.add_node("execution", execution)
    g.add_node("approval", approval)
    g.add_node("finalize", finalize)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["next"],
                            ["research", "analysis", "execution", "finalize"])
    g.add_conditional_edges("research", lambda s: s["next"], ["supervisor", "finalize"])
    g.add_conditional_edges("analysis", lambda s: s["next"], ["supervisor", "finalize"])
    g.add_conditional_edges("execution", lambda s: s["next"], ["approval", "supervisor", "finalize"])
    g.add_edge("approval", "execution")
    g.add_edge("finalize", END)
    return g
