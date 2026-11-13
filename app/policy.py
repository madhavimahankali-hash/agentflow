"""Approval policy: decides which actions need a human, using rules (never the LLM).

Tiers
  low     -> runs automatically
  medium  -> runs automatically in "policy" mode, needs approval in "strict" mode
  high    -> always needs a named approver
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.tools.base import Tool

ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class PolicyDecision:
    tier: str
    requires_approval: bool
    reason: str


def _raise(current: str, new: str) -> str:
    return new if ORDER[new] > ORDER[current] else current


def evaluate(tool: Tool, args: dict, settings: Settings) -> PolicyDecision:
    tier, reasons = tool.risk, [f"base tier for {tool.name} is {tool.risk}"]

    if tool.name == "issue_payment":
        amount = float(args.get("amount", 0))
        if amount > settings.payment_approval_threshold:
            tier = _raise(tier, "high")
            reasons.append(f"payment {amount:,.2f} exceeds threshold {settings.payment_approval_threshold:,.0f}")

    if tool.name == "send_email":
        domain = str(args.get("to", "")).rsplit("@", 1)[-1].lower()
        if domain == settings.internal_email_domain:
            tier = "low"
            reasons.append("internal recipient")
        else:
            reasons.append(f"external recipient ({domain})")

    if tool.name == "erp_set_bank_details":
        reasons.append("bank detail changes are the main target of invoice fraud")

    if not tool.writes:
        tier = "low"

    requires = tier == "high" or (tier == "medium" and settings.approval_mode == "strict")
    return PolicyDecision(tier=tier, requires_approval=requires, reason="; ".join(reasons))
