"""FastAPI service layer.

POST /runs                      start a run (returns immediately; work runs in the background)
GET  /runs, /runs/{id}          run status, plan, results, pending approval
GET  /runs/{id}/history         full execution history (events + approvals)
GET  /approvals?status=pending  approval inbox
POST /approvals/{id}            approve / reject (optionally with edited args) -> resumes the run
GET  /tools, /systems, /graph   tool catalog, mock system state, graph diagram (Mermaid)
GET  /                          demo UI

Run with:  uvicorn app.api:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.models import ApprovalDecision, StartRunRequest
from app.runner import ApprovalConflict, Runner
from app.tools.base import catalog

app = FastAPI(title="AgentFlow: multi-agent workflow automation", version="1.0.0")
_runner: Runner | None = None


def runner() -> Runner:
    global _runner
    if _runner is None:
        _runner = Runner()
    return _runner


def set_runner(r: Runner) -> None:  # used by tests
    global _runner
    _runner = r


@app.get("/health")
def health():
    return {"status": "ok", "llm_provider": runner().settings.llm_provider}


@app.post("/runs", status_code=202)
def start_run(body: StartRunRequest, background: BackgroundTasks):
    r = runner()
    run_id = r.create_run(body.request, body.inputs, body.requested_by)
    background.add_task(r.execute, run_id)
    return {"run_id": run_id, "status": "running", "links": {"self": f"/runs/{run_id}",
                                                             "history": f"/runs/{run_id}/history"}}


@app.get("/runs")
def list_runs(limit: int = 50):
    return runner().history.list_runs(limit)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    r = runner()
    run = r.history.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return {**run, "state": r.state(run_id)}


@app.get("/runs/{run_id}/history")
def run_history(run_id: str):
    r = runner()
    if not r.history.get_run(run_id):
        raise HTTPException(404, "run not found")
    return {"run_id": run_id, "events": r.history.events(run_id),
            "approvals": r.history.list_approvals(run_id=run_id)}


@app.get("/approvals")
def list_approvals(status: str | None = "pending"):
    r = runner()
    r.expire_stale_approvals()
    return r.history.list_approvals(status=status)


@app.post("/approvals/{approval_id}")
def decide(approval_id: str, body: ApprovalDecision, background: BackgroundTasks):
    r = runner()
    try:
        run_id = r.decide(approval_id, body.decision, body.approver, body.comment, body.edited_args)
    except ApprovalConflict as e:
        raise HTTPException(409, str(e)) from e
    background.add_task(r.resume, run_id, body.decision, body.approver, body.comment, body.edited_args)
    return {"approval_id": approval_id, "run_id": run_id, "decision": body.decision, "status": "resuming"}


@app.get("/tools")
def tools():
    return catalog()


@app.get("/systems")
def systems():
    return runner().systems.snapshot()


@app.get("/graph")
def graph():
    return {"mermaid": runner().mermaid()}


@app.get("/")
def ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
