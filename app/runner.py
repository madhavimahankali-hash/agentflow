"""Starts, resumes and inspects runs. Shared by the FastAPI app and the AgentCore entrypoint."""
from __future__ import annotations

import sqlite3
import threading
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import app.tools  # noqa: F401  (registers tools)
from app.config import Settings, get_settings
from app.graph import build_graph
from app.history import HistoryStore
from app.tools.mock_systems import MockSystems


class ApprovalConflict(Exception):
    """Approval does not exist or was already decided."""


class Runner:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.history = HistoryStore(self.settings.history_db)
        self.systems = MockSystems(self.settings.systems_db)
        self._checkpoint_conn = sqlite3.connect(str(self.settings.checkpoint_db), check_same_thread=False)
        self.checkpointer = SqliteSaver(self._checkpoint_conn)
        self.graph = build_graph(self.settings, self.history, self.systems).compile(checkpointer=self.checkpointer)
        self._locks: dict[str, threading.Lock] = {}

    def close(self) -> None:
        """Release database connections (tests, graceful shutdown)."""
        self.history.close()
        self.systems.close()
        self._checkpoint_conn.close()

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 200}

    def _lock(self, run_id: str) -> threading.Lock:
        return self._locks.setdefault(run_id, threading.Lock())

    # ---- lifecycle -----------------------------------------------------
    def create_run(self, request: str, inputs: dict, requested_by: str) -> str:
        run_id = "run_" + uuid.uuid4().hex[:12]
        self.history.create_run(run_id, request, inputs, requested_by)
        self.history.record_event(run_id, "run_started", agent="api", request=request,
                                  requested_by=requested_by, llm_provider=self.settings.llm_provider)
        return run_id

    def execute(self, run_id: str) -> str:
        run = self.history.get_run(run_id)
        initial = {"run_id": run_id, "request": run["request"], "inputs": run["inputs"],
                   "status": "running", "plan": None}
        return self._drive(run_id, initial)

    def start(self, request: str, inputs: dict | None = None, requested_by: str = "demo.user@example.com") -> str:
        """Create + execute synchronously (used by tests, CLI and AgentCore)."""
        run_id = self.create_run(request, inputs or {}, requested_by)
        self.execute(run_id)
        return run_id

    def decide(self, approval_id: str, decision: str, approver: str, comment: str = "",
               edited_args: dict | None = None) -> str:
        approval = self.history.get_approval(approval_id)
        if not approval:
            raise ApprovalConflict(f"approval {approval_id} not found")
        status = {"approve": "approved", "reject": "rejected", "expire": "expired"}[decision]
        if not self.history.decide_approval(approval_id, status, approver, comment, edited_args):
            raise ApprovalConflict(f"approval {approval_id} is already {approval['status']}")
        return approval["run_id"]

    def resume(self, run_id: str, decision: str, approver: str, comment: str = "",
               edited_args: dict | None = None) -> str:
        payload = {"decision": decision, "approver": approver, "comment": comment, "edited_args": edited_args}
        return self._drive(run_id, Command(resume=payload))

    def _drive(self, run_id: str, graph_input) -> str:
        with self._lock(run_id):
            try:
                self.graph.invoke(graph_input, self._config(run_id))
            except Exception as e:
                self.history.record_event(run_id, "run_failed", agent="runner", error=f"{type(e).__name__}: {e}")
                self.history.update_run(run_id, "failed", f"Unexpected error: {e}")
                return "failed"
            snap = self.graph.get_state(self._config(run_id))
            if snap.next:  # paused at an interrupt
                self.history.update_run(run_id, "awaiting_approval")
                return "awaiting_approval"
            return snap.values.get("status", "completed")

    def expire_stale_approvals(self) -> list[str]:
        expired = []
        for a in self.history.stale_approvals(self.settings.approval_ttl_hours):
            try:
                run_id = self.decide(a["id"], "expire", "system", "approval expired")
            except ApprovalConflict:
                continue
            self.resume(run_id, "expire", "system", f"no decision within {self.settings.approval_ttl_hours}h")
            expired.append(a["id"])
        return expired

    # ---- inspection ----------------------------------------------------
    def state(self, run_id: str) -> dict:
        snap = self.graph.get_state(self._config(run_id))
        v = snap.values or {}
        return {k: v.get(k) for k in ("plan", "planner", "facts", "results", "pending", "status",
                                      "halt_reason", "summary", "cursor")}

    def mermaid(self) -> str:
        return self.graph.get_graph().draw_mermaid()
