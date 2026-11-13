"""Tool registry.

A tool = one external capability (API call) with:
  * an owner agent (least privilege: agents only see their own tools),
  * a base risk tier (low / medium / high) used by the approval policy,
  * a Pydantic args model (inputs are validated BEFORE any API call).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from app.config import Settings
from app.tools.mock_systems import MockSystems


@dataclass
class ToolContext:
    settings: Settings
    systems: MockSystems
    run_id: str
    facts: dict[str, Any] = field(default_factory=dict)  # results gathered so far in this run


@dataclass(frozen=True)
class Tool:
    name: str
    agent: str                      # research | analysis | execution
    description: str
    risk: str                       # low | medium | high  (base tier; policy may raise it)
    args_model: type[BaseModel]
    func: Callable[[ToolContext, Any], dict]
    writes: bool = False            # True = changes data in a business system

    def run(self, ctx: ToolContext, raw_args: dict) -> dict:
        args = self.args_model.model_validate(raw_args)   # raises ValidationError on bad input
        result = self.func(ctx, args)
        if "summary" not in result:
            result["summary"] = f"{self.name} done"
        return result


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    _REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown tool '{name}'")
    return _REGISTRY[name]


def tools_for(agent: str) -> list[Tool]:
    return [t for t in _REGISTRY.values() if t.agent == agent]


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def catalog() -> list[dict]:
    """Machine-readable tool catalog (used by the planner prompt and GET /tools)."""
    return [
        {
            "name": t.name,
            "agent": t.agent,
            "risk": t.risk,
            "writes": t.writes,
            "description": t.description,
            "args": t.args_model.model_json_schema().get("properties", {}),
        }
        for t in _REGISTRY.values()
    ]
