"""Exercises the Bedrock code paths with a scripted stand-in model (no AWS calls)."""
from langchain_core.messages import AIMessage

from app.agents import supervisor, workers
from app.agents.supervisor import LLMPlan
from app.agents.workflows import weekly_pipeline_report
from app.models import Plan, PlanStep
from app.runner import Runner

USAGE = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}


def ai(content="", **kw):
    return AIMessage(content=content, usage_metadata=USAGE, **kw)


class ScriptedModel:
    """Mimics the parts of ChatBedrockConverse the agents use."""

    plans: list = []

    def with_structured_output(self, schema, include_raw=False):
        plans = list(self.plans) or [LLMPlan(plan=weekly_pipeline_report(), extracted_inputs={"week": "2026-W45"})]

        class _S:
            def invoke(self, messages):
                parsed = plans.pop(0) if len(plans) > 1 else plans[0]
                return {"raw": ai("plan"), "parsed": parsed, "parsing_error": None}
        return _S()

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        last = messages[-1]
        if getattr(last, "type", "") == "tool":
            return ai("Pipeline improved: one deal won, one lost.")
        if isinstance(messages[0], tuple):  # summary prompt
            return ai([{"type": "text", "text": "Report posted to #sales."}])
        return ai("", tool_calls=[{"name": "sales_pipeline_summary", "args": {"week": "2026-W45"}, "id": "t1"}])


def use(monkeypatch, model):
    monkeypatch.setattr(supervisor, "chat_model", lambda role, s: model)
    monkeypatch.setattr(workers, "chat_model", lambda role, s: model)


def test_bedrock_paths_with_scripted_model(settings, monkeypatch):
    use(monkeypatch, ScriptedModel())
    r = Runner(settings)
    run_id = r.start("Can you share how the pipeline moved this week (2026-W45) with sales?", {})
    st = r.state(run_id)
    assert st["status"] == "completed"
    assert st["planner"] == "bedrock"
    assert st["results"][0]["note"] == "Pipeline improved: one deal won, one lost."
    assert st["summary"] == "Report posted to #sales."
    calls = [e for e in r.history.events(run_id) if e["type"] == "llm_call"]
    assert {c["data"]["purpose"] for c in calls} >= {"plan_attempt_1", "reasoning_round_1", "summary"}
    assert all(c["data"]["input_tokens"] == 120 for c in calls)
    r.close()


def test_planner_self_corrects_an_invalid_plan(settings, monkeypatch):
    bad = Plan(goal="x", steps=[PlanStep(id="s1", agent="research", action="slack_notify",
                                         args={"text": "hi"}, description="research agent trying to write")])

    class Corrects(ScriptedModel):
        plans = [LLMPlan(plan=bad), LLMPlan(plan=weekly_pipeline_report(), extracted_inputs={"week": "2026-W45"})]

    use(monkeypatch, Corrects())
    r = Runner(settings)
    run_id = r.start("Share the pipeline for 2026-W45", {})
    assert r.state(run_id)["planner"] == "bedrock (self-corrected on attempt 2)"
    assert r.state(run_id)["status"] == "completed"
    r.close()


def test_invalid_llm_plan_falls_back_to_template(settings, monkeypatch):
    class BadPlanner(ScriptedModel):
        def with_structured_output(self, schema, include_raw=False):
            class _S:
                def invoke(self, messages):
                    raise ValueError("model returned junk")
            return _S()

    monkeypatch.setattr(supervisor, "chat_model", lambda role, s: BadPlanner())
    r = Runner(settings)
    run_id = r.start("Send the weekly pipeline report", {"week": "2026-W45"})
    assert r.state(run_id)["planner"].startswith("template (LLM fallback")
    r.close()
