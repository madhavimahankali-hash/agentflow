"""Typed structures shared by the supervisor, agents and API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["research", "analysis", "execution"]


class PlanStep(BaseModel):
    id: str = Field(description="Short unique id, e.g. 's1'")
    agent: AgentName = Field(description="Specialist agent that owns this step")
    action: str = Field(description="Name of the tool the agent should use")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments. Use {{key}} to reference request inputs or earlier results.",
    )
    description: str = Field(description="What this step achieves, in one sentence")


class Plan(BaseModel):
    goal: str
    workflow: str = Field(default="custom", description="Name of the workflow, e.g. vendor_onboarding")
    steps: list[PlanStep]


class StartRunRequest(BaseModel):
    request: str = Field(description="What the user wants done, in plain language")
    inputs: dict[str, Any] = Field(default_factory=dict, description="Structured inputs (form fields)")
    requested_by: str = "demo.user@example.com"


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    approver: str = "approver@example.com"
    comment: str = ""
    edited_args: dict[str, Any] | None = Field(
        default=None, description="Optional corrected arguments; replaces the proposed ones"
    )
