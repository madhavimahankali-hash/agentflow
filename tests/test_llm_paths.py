"""Exercises the Bedrock code paths with a scripted stand-in model (no AWS calls)."""
from langchain_core.messages import AIMessage

from app.agents import supervisor, workers
from app.agents.supervisor import LLMPlan
from app.agents.workflows import weekly_pipeline_report
from app.runner import Runner


class ScriptedModel:
    """Mimics the parts of ChatBedrockConverse the agents use."""

    def __init__(self):
        self.calls = 0

    def with_structured_output(self, schema):
        model = self

        class _S:
            def invoke(self, messages):
                # LLM planner extracts the week from free text
                return LLMPlan(plan=weekly_pipeline_report(), extracted_inputs={"week": "2026-W45"})
        return _S()

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        last = messages[-1]
        if getattr(last, "type", "") == "tool":
            return AIMessage(content="Pipeline improved: one deal won, one lost.")
        if isinstance(messages[0], tuple):  # summary prompt
            return AIMessage(content=[{"type": "text", "text": "Report posted to #sales."}])
        return AIMessage(content="", tool_calls=[{"name": "sales_pipeline_summary",
                                                  "args": {"week": "2026-W45"}, "id": "t1"}])


def test_bedrock_paths_with_scripted_model(settings, monkeypatch):
    model = ScriptedModel()
    monkeypatch.setattr(supervisor, "chat_model", lambda role, s: model)
    monkeypatch.setattr(workers, "chat_model", lambda role, s: model)
    r = Runner(settings)
    run_id = r.start("Can you share how the pipeline moved this week (2026-W45) with sales?", {})
    st = r.state(run_id)
    assert st["status"] == "completed"
    assert st["planner"] == "bedrock"
    assert st["results"][0]["note"] == "Pipeline improved: one deal won, one lost."
    assert st["summary"] == "Report posted to #sales."


def test_invalid_llm_plan_falls_back_to_template(settings, monkeypatch):
    class BadPlanner(ScriptedModel):
        def with_structured_output(self, schema):
            class _S:
                def invoke(self, messages):
                    raise ValueError("model returned junk")
            return _S()

    monkeypatch.setattr(supervisor, "chat_model", lambda role, s: BadPlanner())
    r = Runner(settings)
    run_id = r.start("Send the weekly pipeline report", {"week": "2026-W45"})
    assert r.state(run_id)["planner"].startswith("template (LLM fallback")
