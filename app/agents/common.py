"""Helpers shared by all agents: argument templating and resilient tool calls."""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.tools.base import Tool, ToolContext

_TOKEN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class MissingValue(KeyError):
    pass


def resolve(obj: Any, facts: dict) -> Any:
    """Replace {{key}} references with values from facts (request inputs + earlier results)."""
    if isinstance(obj, dict):
        return {k: resolve(v, facts) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v, facts) for v in obj]
    if isinstance(obj, str):
        whole = _TOKEN.fullmatch(obj.strip())
        if whole:  # keep the original type (number, dict) for a lone reference
            key = whole.group(1)
            if key not in facts:
                raise MissingValue(key)
            return facts[key]

        def sub(m: re.Match) -> str:
            if m.group(1) not in facts:
                raise MissingValue(m.group(1))
            return str(facts[m.group(1)])

        return _TOKEN.sub(sub, obj)
    return obj


RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError, ConnectionError, TimeoutError)


def call_tool(tool: Tool, ctx: ToolContext, args: dict, attempts: int = 3, backoff: float = 0.5) -> tuple[dict, int]:
    """Run a tool with retries on transient errors. Returns (result, latency_ms).

    Validation errors and business errors (ValueError) are NOT retried: retrying
    a bad request only repeats the failure.
    """
    start = time.perf_counter()
    for attempt in range(1, attempts + 1):
        try:
            result = tool.run(ctx, args)
            return result, int((time.perf_counter() - start) * 1000)
        except RETRYABLE:
            if attempt == attempts:
                raise
            time.sleep(backoff * 2 ** (attempt - 1))
    raise RuntimeError("unreachable")


FACT_EXCLUDE = {"summary", "source"}


def facts_from(result: dict) -> dict:
    return {k: v for k, v in result.items() if k not in FACT_EXCLUDE}
