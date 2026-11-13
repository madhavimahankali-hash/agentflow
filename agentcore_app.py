"""Entrypoint for Amazon Bedrock AgentCore Runtime.

AgentCore Runtime hosts this agent and calls `invoke` with a JSON payload:

  {"action": "start",  "request": "...", "inputs": {...}, "requested_by": "..."}
  {"action": "decide", "approval_id": "...", "decision": "approve"|"reject", "approver": "...", "comment": "..."}
  {"action": "status", "run_id": "..."}

Deploy (from this folder, with AWS credentials configured):
  pip install bedrock-agentcore bedrock-agentcore-starter-toolkit
  agentcore configure --entrypoint agentcore_app.py
  agentcore launch
  agentcore invoke '{"action": "start", "request": "Send the weekly pipeline report", "inputs": {"week": "2026-W45"}}'

NOTE: SQLite files are local to the runtime session. For production, point the
checkpointer and history store at a shared database (e.g. DynamoDB or Aurora Postgres
via langgraph-checkpoint-postgres) so a decision can resume a run from any session.
"""
from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from app.runner import ApprovalConflict, Runner

app = BedrockAgentCoreApp()
runner = Runner()


@app.entrypoint
def invoke(payload: dict) -> dict:
    action = payload.get("action", "start")
    if action == "start":
        run_id = runner.start(payload["request"], payload.get("inputs", {}),
                              payload.get("requested_by", "agentcore"))
        return {"run_id": run_id, **runner.state(run_id)}
    if action == "decide":
        try:
            run_id = runner.decide(payload["approval_id"], payload["decision"],
                                   payload.get("approver", "unknown"), payload.get("comment", ""),
                                   payload.get("edited_args"))
        except ApprovalConflict as e:
            return {"error": str(e)}
        runner.resume(run_id, payload["decision"], payload.get("approver", "unknown"),
                      payload.get("comment", ""), payload.get("edited_args"))
        return {"run_id": run_id, **runner.state(run_id)}
    if action == "status":
        return {"run": runner.history.get_run(payload["run_id"]), **runner.state(payload["run_id"])}
    return {"error": f"unknown action {action}"}


if __name__ == "__main__":
    app.run()
