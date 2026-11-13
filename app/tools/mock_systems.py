"""Simulated business systems (ERP, Jira, email outbox, Slack log, data warehouse).

Everything lives in one SQLite file so the demo can show real state changes
(GET /systems). Each class mirrors the shape of a real API client, so swapping a
mock for the real client means changing one adapter, not the agents.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_vendors (
    id TEXT PRIMARY KEY, name TEXT, country TEXT, category TEXT, contact_email TEXT,
    status TEXT, iban TEXT, account_name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS erp_payments (
    id TEXT PRIMARY KEY, vendor_id TEXT, amount REAL, currency TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jira_tickets (
    key TEXT PRIMARY KEY, project TEXT, summary TEXT, description TEXT, status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS email_outbox (
    id TEXT PRIMARY KEY, recipient TEXT, subject TEXT, body TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS slack_log (
    id TEXT PRIMARY KEY, channel TEXT, text TEXT, delivered_via TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- "Data warehouse" tables
CREATE TABLE IF NOT EXISTS dw_vendor_rates (vendor TEXT, category TEXT, rate REAL, unit TEXT);
CREATE TABLE IF NOT EXISTS dw_budgets (category TEXT PRIMARY KEY, annual_budget REAL, committed REAL);
CREATE TABLE IF NOT EXISTS dw_pipeline (
    week TEXT, deal TEXT, stage TEXT, amount REAL, owner TEXT, prev_stage TEXT
);
"""

SEED_RATES = [
    ("BlueLine Logistics", "logistics", 2.10, "USD/kg"),
    ("Atlas Freight", "logistics", 2.35, "USD/kg"),
    ("Northwind Cargo", "logistics", 1.95, "USD/kg"),
    ("CloudPrint Co", "printing", 0.08, "USD/page"),
    ("PaperTrail Inc", "printing", 0.07, "USD/page"),
]
SEED_BUDGETS = [("logistics", 500000.0, 380000.0), ("printing", 40000.0, 12000.0), ("it_services", 250000.0, 240000.0)]
SEED_PIPELINE = [
    ("2026-W45", "Acme renewal", "Negotiation", 120000, "Priya", "Proposal"),
    ("2026-W45", "Globex expansion", "Closed Won", 85000, "Omar", "Negotiation"),
    ("2026-W45", "Initech pilot", "Proposal", 30000, "Priya", "Discovery"),
    ("2026-W45", "Umbrella migration", "Closed Lost", 60000, "Lena", "Negotiation"),
    ("2026-W45", "Hooli data platform", "Discovery", 200000, "Omar", None),
]


class MockSystems:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            if not self._conn.execute("SELECT 1 FROM dw_vendor_rates LIMIT 1").fetchone():
                self._conn.executemany("INSERT INTO dw_vendor_rates VALUES (?,?,?,?)", SEED_RATES)
                self._conn.executemany("INSERT INTO dw_budgets VALUES (?,?,?)", SEED_BUDGETS)
                self._conn.executemany("INSERT INTO dw_pipeline VALUES (?,?,?,?,?,?)", SEED_PIPELINE)
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount

    # ---- ERP -----------------------------------------------------------
    def erp_create_vendor(self, name: str, country: str, category: str, contact_email: str) -> str:
        vendor_id = "V-" + uuid.uuid4().hex[:6].upper()
        self.write(
            "INSERT INTO erp_vendors (id, name, country, category, contact_email, status) "
            "VALUES (?,?,?,?,?, 'inactive')",
            (vendor_id, name, country, category, contact_email),
        )
        return vendor_id

    def erp_vendor(self, vendor_id: str) -> dict | None:
        rows = self.query("SELECT * FROM erp_vendors WHERE id=?", (vendor_id,))
        return rows[0] if rows else None

    def erp_set_bank(self, vendor_id: str, iban: str, account_name: str) -> None:
        if not self.write("UPDATE erp_vendors SET iban=?, account_name=? WHERE id=?", (iban, account_name, vendor_id)):
            raise ValueError(f"Vendor {vendor_id} not found")

    def erp_set_status(self, vendor_id: str, status: str) -> None:
        if not self.write("UPDATE erp_vendors SET status=? WHERE id=?", (status, vendor_id)):
            raise ValueError(f"Vendor {vendor_id} not found")

    def erp_pay(self, vendor_id: str, amount: float, currency: str) -> str:
        pid = "PAY-" + uuid.uuid4().hex[:6].upper()
        self.write("INSERT INTO erp_payments (id, vendor_id, amount, currency) VALUES (?,?,?,?)",
                   (pid, vendor_id, amount, currency))
        return pid

    # ---- Jira ----------------------------------------------------------
    def jira_create(self, project: str, summary: str, description: str) -> str:
        n = len(self.query("SELECT key FROM jira_tickets WHERE project=?", (project,))) + 1
        key = f"{project}-{100 + n}"
        self.write("INSERT INTO jira_tickets (key, project, summary, description, status) VALUES (?,?,?,?, 'Open')",
                   (key, project, summary, description))
        return key

    # ---- Email / Slack -------------------------------------------------
    def email_send(self, to: str, subject: str, body: str) -> str:
        mid = "MSG-" + uuid.uuid4().hex[:8]
        self.write("INSERT INTO email_outbox (id, recipient, subject, body) VALUES (?,?,?,?)", (mid, to, subject, body))
        return mid

    def slack_log(self, channel: str, text: str, via: str) -> str:
        sid = "SLK-" + uuid.uuid4().hex[:8]
        self.write("INSERT INTO slack_log (id, channel, text, delivered_via) VALUES (?,?,?,?)", (sid, channel, text, via))
        return sid

    def snapshot(self) -> dict:
        return {
            "erp_vendors": self.query("SELECT * FROM erp_vendors ORDER BY created_at DESC"),
            "erp_payments": self.query("SELECT * FROM erp_payments ORDER BY created_at DESC"),
            "jira_tickets": self.query("SELECT * FROM jira_tickets ORDER BY created_at DESC"),
            "email_outbox": self.query("SELECT * FROM email_outbox ORDER BY created_at DESC"),
            "slack_log": self.query("SELECT * FROM slack_log ORDER BY created_at DESC"),
        }


# Reference data for the mock research APIs -----------------------------------
COMPANY_REGISTRY = {
    "fastfreight llc": {"legal_name": "FastFreight LLC", "country": "US", "status": "active",
                        "incorporated": "2014-03-11", "registration_no": "DE-5523190"},
    "gulfline shipping": {"legal_name": "Gulfline Shipping FZE", "country": "AE", "status": "active",
                          "incorporated": "2019-08-02", "registration_no": "JAFZA-77821"},
    "shadow cargo ltd": {"legal_name": "Shadow Cargo Ltd", "country": "XX", "status": "dissolved",
                         "incorporated": "2011-01-20", "registration_no": "XX-00412"},
}
SANCTIONED_NAMES = {"shadow cargo ltd", "blackstar trading"}
NEWS = {
    "fastfreight llc": [{"title": "FastFreight opens new hub in Dallas", "sentiment": "neutral"}],
    "shadow cargo ltd": [
        {"title": "Shadow Cargo linked to customs fraud probe", "sentiment": "adverse"},
        {"title": "Regulator fines Shadow Cargo", "sentiment": "adverse"},
    ],
}


def to_json(obj) -> str:
    return json.dumps(obj, default=str)
