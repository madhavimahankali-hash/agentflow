"""Known workflow templates.

Used (a) as the planner in offline mode and (b) as a safe fallback if the LLM
planner returns an invalid plan. Arguments reference request inputs or earlier
results with {{key}}.
"""
from __future__ import annotations

from app.models import Plan, PlanStep

VENDOR_FIELDS = ["vendor_name", "country", "category", "contact_email",
                 "quoted_rate", "annual_spend", "iban", "account_name"]


def vendor_onboarding() -> Plan:
    S = PlanStep
    return Plan(
        goal="Onboard a new vendor safely across Procurement, Finance, Legal and IT",
        workflow="vendor_onboarding",
        steps=[
            S(id="s1", agent="research", action="company_registry_lookup",
              args={"vendor_name": "{{vendor_name}}", "country": "{{country}}"},
              description="Verify the company's legal registration"),
            S(id="s2", agent="research", action="sanctions_screen", args={"name": "{{vendor_name}}"},
              description="Screen the company against sanctions lists"),
            S(id="s3", agent="research", action="news_search", args={"vendor_name": "{{vendor_name}}"},
              description="Check for adverse media"),
            S(id="s4", agent="analysis", action="compare_vendor_rates",
              args={"category": "{{category}}", "quoted_rate": "{{quoted_rate}}"},
              description="Benchmark the quote against existing vendors"),
            S(id="s5", agent="analysis", action="check_budget",
              args={"category": "{{category}}", "annual_spend": "{{annual_spend}}"},
              description="Check the expected spend fits the budget"),
            S(id="s6", agent="analysis", action="score_risk", args={},
              description="Score overall vendor risk; high risk stops the workflow"),
            S(id="s7", agent="execution", action="erp_create_vendor",
              args={"vendor_name": "{{vendor_name}}", "country": "{{country}}",
                    "category": "{{category}}", "contact_email": "{{contact_email}}"},
              description="Create the vendor in the ERP as inactive"),
            S(id="s8", agent="execution", action="jira_create_ticket",
              args={"project": "LEGAL", "summary": "Contract review: {{vendor_name}}",
                    "description": "New {{category}} vendor {{vendor_id}}. Risk: {{risk_level}} ({{risk_score}}/100)."},
              description="Ask Legal to review the contract"),
            S(id="s9", agent="execution", action="erp_set_bank_details",
              args={"vendor_id": "{{vendor_id}}", "iban": "{{iban}}", "account_name": "{{account_name}}"},
              description="Save the vendor's bank details (sensitive: needs approval)"),
            S(id="s10", agent="execution", action="erp_activate_vendor", args={"vendor_id": "{{vendor_id}}"},
              description="Activate the vendor for payment"),
            S(id="s11", agent="execution", action="send_email",
              args={"to": "{{contact_email}}", "subject": "Welcome to our supplier network",
                    "body": "Hello {{vendor_name}}, your supplier account {{vendor_id}} is now active."},
              description="Welcome the supplier"),
            S(id="s12", agent="execution", action="slack_notify",
              args={"channel": "#procurement", "text": "Vendor {{vendor_name}} ({{vendor_id}}) onboarded. Legal: {{ticket_key}}"},
              description="Tell the procurement team"),
        ],
    )


def weekly_pipeline_report() -> Plan:
    S = PlanStep
    return Plan(
        goal="Produce and share the weekly sales pipeline report",
        workflow="weekly_pipeline_report",
        steps=[
            S(id="s1", agent="analysis", action="sales_pipeline_summary", args={"week": "{{week}}"},
              description="Summarise pipeline changes for the week"),
            S(id="s2", agent="execution", action="slack_notify",
              args={"channel": "#sales", "text": "{{pipeline_summary}}"},
              description="Post the summary to the sales channel"),
        ],
    )


TEMPLATES = {
    "vendor_onboarding": (vendor_onboarding, ("onboard", "vendor", "supplier")),
    "weekly_pipeline_report": (weekly_pipeline_report, ("pipeline", "sales report", "weekly report")),
}


def match_template(request: str) -> Plan | None:
    text = request.lower()
    best, best_hits = None, 0
    for factory, keywords in TEMPLATES.values():
        hits = sum(k in text for k in keywords)
        if hits > best_hits:
            best, best_hits = factory, hits
    return best() if best else None
