"""Data Analysis agent tools (read-only queries + calculations)."""
from __future__ import annotations

from pydantic import BaseModel

from app.tools.base import Tool, ToolContext, register


class RatesArgs(BaseModel):
    category: str
    quoted_rate: float


def compare_vendor_rates(ctx: ToolContext, a: RatesArgs) -> dict:
    rows = ctx.systems.query("SELECT vendor, rate, unit FROM dw_vendor_rates WHERE category=?", (a.category,))
    if not rows:
        return {"rate_benchmark": None, "summary": f"No benchmark rates for {a.category}"}
    rates = [r["rate"] for r in rows]
    avg = sum(rates) / len(rates)
    delta = (a.quoted_rate - avg) / avg * 100
    return {
        "rate_benchmark": {"vendors": rows, "average": round(avg, 4), "min": min(rates), "max": max(rates)},
        "rate_delta_pct": round(delta, 1),
        "summary": f"Quote {a.quoted_rate} vs average {avg:.2f} {rows[0]['unit']} ({delta:+.1f}%) across {len(rows)} vendors",
    }


class BudgetArgs(BaseModel):
    category: str
    annual_spend: float


def check_budget(ctx: ToolContext, a: BudgetArgs) -> dict:
    rows = ctx.systems.query("SELECT * FROM dw_budgets WHERE category=?", (a.category,))
    if not rows:
        return {"budget_ok": False, "summary": f"No budget line for {a.category}"}
    b = rows[0]
    remaining = b["annual_budget"] - b["committed"]
    ok = a.annual_spend <= remaining
    return {"budget_ok": ok, "budget_remaining": remaining,
            "summary": f"Expected spend {a.annual_spend:,.0f} vs remaining budget {remaining:,.0f}: "
                       f"{'within budget' if ok else 'OVER budget'}"}


class RiskArgs(BaseModel):
    pass


def score_risk(ctx: ToolContext, a: RiskArgs) -> dict:
    """Transparent rule-based score (0-100) from facts gathered earlier in the run."""
    f, score, reasons = ctx.facts, 0, []
    if f.get("sanctions_hits", 0) > 0:
        score += 70; reasons.append("sanctions match")
    if f.get("registry_status") not in (None, "active"):
        score += 30; reasons.append(f"registry status '{f.get('registry_status')}'")
    if f.get("adverse_news", 0) > 0:
        score += 15 * f["adverse_news"]; reasons.append(f"{f['adverse_news']} adverse news item(s)")
    if (f.get("rate_delta_pct") or 0) > 15:
        score += 10; reasons.append("price >15% above benchmark")
    if f.get("budget_ok") is False:
        score += 15; reasons.append("over budget")
    score = min(score, 100)
    level = "high" if score >= 50 else "medium" if score >= 20 else "low"
    return {"risk_score": score, "risk_level": level, "risk_reasons": reasons,
            "summary": f"Risk {level} ({score}/100)" + (f": {', '.join(reasons)}" if reasons else "")}


class PipelineArgs(BaseModel):
    week: str


def sales_pipeline_summary(ctx: ToolContext, a: PipelineArgs) -> dict:
    rows = ctx.systems.query("SELECT * FROM dw_pipeline WHERE week=?", (a.week,))
    won = sum(r["amount"] for r in rows if r["stage"] == "Closed Won")
    lost = sum(r["amount"] for r in rows if r["stage"] == "Closed Lost")
    open_ = sum(r["amount"] for r in rows if not r["stage"].startswith("Closed"))
    moved = [f"{r['deal']}: {r['prev_stage']} -> {r['stage']}" for r in rows if r["prev_stage"]]
    text = (f"Pipeline {a.week}: won ${won:,.0f}, lost ${lost:,.0f}, open ${open_:,.0f}. "
            f"Stage changes: {'; '.join(moved) or 'none'}.")
    return {"pipeline_summary": text, "pipeline_rows": rows, "summary": text}


register(Tool("compare_vendor_rates", "analysis",
              "Compare a quoted rate with existing vendors' rates in the data warehouse.",
              "low", RatesArgs, compare_vendor_rates))
register(Tool("check_budget", "analysis",
              "Check expected annual spend against the remaining budget for a category.",
              "low", BudgetArgs, check_budget))
register(Tool("score_risk", "analysis",
              "Compute a vendor risk score from the research and analysis results gathered so far. Returns risk_score, risk_level.",
              "low", RiskArgs, score_risk))
register(Tool("sales_pipeline_summary", "analysis",
              "Summarise the sales pipeline for a given ISO week (e.g. 2026-W45). Returns pipeline_summary.",
              "low", PipelineArgs, sales_pipeline_summary))
