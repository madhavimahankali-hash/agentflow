"""Task Execution agent tools: the ONLY tools that change data in business systems."""
from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from app.tools.base import Tool, ToolContext, register


class CreateVendorArgs(BaseModel):
    vendor_name: str
    country: str
    category: str
    contact_email: str


def erp_create_vendor(ctx: ToolContext, a: CreateVendorArgs) -> dict:
    vid = ctx.systems.erp_create_vendor(a.vendor_name, a.country, a.category, a.contact_email)
    return {"vendor_id": vid, "summary": f"Created vendor {vid} ({a.vendor_name}) in ERP as inactive"}


class BankArgs(BaseModel):
    vendor_id: str
    iban: str = Field(min_length=15, max_length=34)
    account_name: str


def erp_set_bank_details(ctx: ToolContext, a: BankArgs) -> dict:
    iban = a.iban.replace(" ", "")
    ctx.systems.erp_set_bank(a.vendor_id, iban, a.account_name)
    masked = iban[:4] + "****" + iban[-4:]
    return {"bank_details_set": True, "summary": f"Saved bank account {masked} for {a.vendor_id}"}


class VendorIdArgs(BaseModel):
    vendor_id: str


def erp_activate_vendor(ctx: ToolContext, a: VendorIdArgs) -> dict:
    v = ctx.systems.erp_vendor(a.vendor_id)
    if not v:
        raise ValueError(f"Vendor {a.vendor_id} not found")
    if not v.get("iban"):
        raise ValueError("Cannot activate a vendor without approved bank details")
    ctx.systems.erp_set_status(a.vendor_id, "active")
    return {"vendor_active": True, "summary": f"Activated vendor {a.vendor_id}"}


class PaymentArgs(BaseModel):
    vendor_id: str
    amount: float = Field(gt=0)
    currency: str = "USD"


def issue_payment(ctx: ToolContext, a: PaymentArgs) -> dict:
    pid = ctx.systems.erp_pay(a.vendor_id, a.amount, a.currency)
    return {"payment_id": pid, "summary": f"Issued payment {pid}: {a.amount:,.2f} {a.currency} to {a.vendor_id}"}


class TicketArgs(BaseModel):
    project: str
    summary: str
    description: str = ""


def jira_create_ticket(ctx: ToolContext, a: TicketArgs) -> dict:
    key = ctx.systems.jira_create(a.project, a.summary, a.description)
    return {"ticket_key": key, "summary": f"Opened {key}: {a.summary}"}


class EmailArgs(BaseModel):
    to: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str
    body: str


def send_email(ctx: ToolContext, a: EmailArgs) -> dict:
    mid = ctx.systems.email_send(str(a.to), a.subject, a.body)
    return {"email_id": mid, "summary": f"Sent email to {a.to}: {a.subject}"}


class SlackArgs(BaseModel):
    channel: str = "#ops"
    text: str


def slack_notify(ctx: ToolContext, a: SlackArgs) -> dict:
    url = ctx.settings.slack_webhook_url
    via = "mock"
    if url:  # real Slack incoming webhook
        httpx.post(url, json={"text": f"[{a.channel}] {a.text}"}, timeout=10).raise_for_status()
        via = "slack_webhook"
    sid = ctx.systems.slack_log(a.channel, a.text, via)
    return {"slack_message_id": sid, "summary": f"Posted to {a.channel} via {via}"}


register(Tool("erp_create_vendor", "execution",
              "Create a vendor record in the ERP (status inactive). Returns vendor_id.",
              "low", CreateVendorArgs, erp_create_vendor, writes=True))
register(Tool("erp_set_bank_details", "execution", "Save or change a vendor's bank account for payments.",
              "high", BankArgs, erp_set_bank_details, writes=True))
register(Tool("erp_activate_vendor", "execution", "Activate a vendor so it can be paid.",
              "medium", VendorIdArgs, erp_activate_vendor, writes=True))
register(Tool("issue_payment", "execution", "Issue a payment to a vendor.",
              "medium", PaymentArgs, issue_payment, writes=True))
register(Tool("jira_create_ticket", "execution",
              "Open a Jira ticket (e.g. LEGAL contract review, IT access). Returns ticket_key.",
              "low", TicketArgs, jira_create_ticket, writes=True))
register(Tool("send_email", "execution", "Send an email.",
              "medium", EmailArgs, send_email, writes=True))
register(Tool("slack_notify", "execution", "Post a message to a Slack channel.",
              "low", SlackArgs, slack_notify, writes=True))
