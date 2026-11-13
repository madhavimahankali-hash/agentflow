"""Model access. Amazon Bedrock in production; None in offline "fake" mode.

Every caller has a deterministic fallback when the model is None, so the whole
system runs and is testable without AWS credentials.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import Settings


@lru_cache(maxsize=8)
def _bedrock(model_id: str, region: str, temperature: float | None = None):
    from langchain_aws import ChatBedrockConverse  # imported lazily: not needed offline

    kwargs = {"temperature": temperature} if temperature is not None else {}
    return ChatBedrockConverse(model=model_id, region_name=region, max_tokens=2048, **kwargs)


def chat_model(role: str, settings: Settings):
    """role: 'supervisor' (planning, final summary) or 'worker' (research/analysis reasoning)."""
    if settings.llm_provider != "bedrock":
        return None
    model_id = settings.supervisor_model_id if role == "supervisor" else settings.worker_model_id
    return _bedrock(model_id, settings.aws_region, settings.llm_temperature)


def text_of(message: Any) -> str:
    """Bedrock Converse can return content as a list of blocks; flatten to text."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts).strip()


def usage_of(message: Any) -> dict:
    """Token usage reported by the model (LangChain usage_metadata), or zeros."""
    u = getattr(message, "usage_metadata", None) or {}
    return {"input_tokens": int(u.get("input_tokens", 0) or 0),
            "output_tokens": int(u.get("output_tokens", 0) or 0)}


def record_llm_call(history, run_id: str, agent: str, step_id: str | None, purpose: str,
                    model_id: str, message: Any, latency_ms: int) -> None:
    """One history event per model call: who called it, why, tokens and latency (cost visibility)."""
    history.record_event(run_id, "llm_call", agent=agent, step_id=step_id, purpose=purpose,
                         model=model_id, latency_ms=latency_ms, **usage_of(message))
