"""Execution history: runs, step events, approvals and idempotency records.

Append-only event log in SQLite. Swap for DynamoDB/Postgres in production;
the interface (record_event, create_approval, ...) stays the same.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    inputs TEXT NOT NULL,
    requested_by TEXT,
    status TEXT NOT NULL,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    agent TEXT,
    step_id TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    tier TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence TEXT,
    status TEXT NOT NULL,          -- pending | approved | rejected | expired
    decided_by TEXT,
    comment TEXT,
    edited_args TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class HistoryStore:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---- helpers -------------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._decode(dict(r)) for r in rows]

    @staticmethod
    def _decode(row: dict) -> dict:
        for k in ("inputs", "data", "args", "evidence", "edited_args", "result"):
            if k in row and isinstance(row[k], str):
                try:
                    row[k] = json.loads(row[k])
                except json.JSONDecodeError:
                    pass
        return row

    # ---- runs ----------------------------------------------------------
    def create_run(self, run_id: str, request: str, inputs: dict, requested_by: str) -> None:
        ts = now()
        self._exec(
            "INSERT INTO runs (id, request, inputs, requested_by, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (run_id, request, json.dumps(inputs), requested_by, ts, ts),
        )

    def update_run(self, run_id: str, status: str, summary: str | None = None) -> None:
        if summary is None:
            self._exec("UPDATE runs SET status=?, updated_at=? WHERE id=?", (status, now(), run_id))
        else:
            self._exec(
                "UPDATE runs SET status=?, summary=?, updated_at=? WHERE id=?",
                (status, summary, now(), run_id),
            )

    def get_run(self, run_id: str) -> dict | None:
        rows = self._all("SELECT * FROM runs WHERE id=?", (run_id,))
        return rows[0] if rows else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        return self._all("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))

    # ---- events --------------------------------------------------------
    def record_event(
        self, run_id: str, type_: str, agent: str | None = None, step_id: str | None = None, **data: Any
    ) -> None:
        self._exec(
            "INSERT INTO events (run_id, ts, type, agent, step_id, data) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, now(), type_, agent, step_id, json.dumps(data, default=str)),
        )

    def events(self, run_id: str) -> list[dict]:
        return self._all("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))

    # ---- approvals -----------------------------------------------------
    def create_approval(
        self, approval_id: str, run_id: str, step_id: str, tool: str, args: dict,
        tier: str, reason: str, evidence: dict,
    ) -> bool:
        """Create a pending approval. Idempotent: returns False if it already exists."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO approvals (id, run_id, step_id, tool, args, tier, reason, evidence, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (approval_id, run_id, step_id, tool, json.dumps(args), tier, reason,
                 json.dumps(evidence, default=str), now()),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def decide_approval(
        self, approval_id: str, status: str, decided_by: str, comment: str = "",
        edited_args: dict | None = None,
    ) -> bool:
        """Record a decision. Returns False if the approval was not pending (prevents double-decisions)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE approvals SET status=?, decided_by=?, comment=?, edited_args=?, decided_at=? "
                "WHERE id=? AND status='pending'",
                (status, decided_by, comment, json.dumps(edited_args) if edited_args else None,
                 now(), approval_id),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def get_approval(self, approval_id: str) -> dict | None:
        rows = self._all("SELECT * FROM approvals WHERE id=?", (approval_id,))
        return rows[0] if rows else None

    def list_approvals(self, status: str | None = None, run_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM approvals WHERE 1=1", []
        if status:
            sql += " AND status=?"
            params.append(status)
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        return self._all(sql + " ORDER BY created_at DESC", tuple(params))

    def stale_approvals(self, ttl_hours: float) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat(timespec="milliseconds")
        return self._all(
            "SELECT * FROM approvals WHERE status='pending' AND created_at < ?", (cutoff,)
        )

    # ---- idempotency ---------------------------------------------------
    def get_idempotent(self, key: str) -> dict | None:
        rows = self._all("SELECT * FROM idempotency WHERE key=?", (key,))
        return rows[0]["result"] if rows else None

    def save_idempotent(self, key: str, tool: str, result: dict) -> None:
        self._exec(
            "INSERT OR REPLACE INTO idempotency (key, tool, result, created_at) VALUES (?, ?, ?, ?)",
            (key, tool, json.dumps(result, default=str), now()),
        )
