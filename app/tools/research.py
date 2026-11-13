"""Research agent tools (read-only)."""
from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from app.tools.base import Tool, ToolContext, register
from app.tools.mock_systems import COMPANY_REGISTRY, NEWS, SANCTIONED_NAMES


class RegistryArgs(BaseModel):
    vendor_name: str
    country: str = ""


def company_registry_lookup(ctx: ToolContext, a: RegistryArgs) -> dict:
    rec = COMPANY_REGISTRY.get(a.vendor_name.strip().lower())
    if not rec:
        return {"registry_found": False, "registry_status": "not_found",
                "summary": f"No registry record found for {a.vendor_name}"}
    return {"registry_found": True, "registry_status": rec["status"], "registry": rec,
            "summary": f"{rec['legal_name']} is {rec['status']}, incorporated {rec['incorporated']} ({rec['country']})",
            "source": "mock company registry"}


class SanctionsArgs(BaseModel):
    name: str = Field(description="Company or person name to screen")


def sanctions_screen(ctx: ToolContext, a: SanctionsArgs) -> dict:
    key = ctx.settings.opensanctions_api_key
    if key:  # real API: https://api.opensanctions.org (free key for non-commercial use)
        resp = httpx.post(
            "https://api.opensanctions.org/match/default",
            headers={"Authorization": f"ApiKey {key}"},
            json={"queries": {"q1": {"schema": "Company", "properties": {"name": [a.name]}}}},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()["responses"]["q1"]["results"]
        hits = [{"caption": r.get("caption"), "score": r.get("score"), "datasets": r.get("datasets", [])[:3]}
                for r in results if r.get("match")]
        source = "OpenSanctions API"
    else:
        hits = [{"caption": a.name, "score": 0.99, "datasets": ["mock_sanctions_list"]}] \
            if a.name.strip().lower() in SANCTIONED_NAMES else []
        source = "mock sanctions list"
    return {"sanctions_hits": len(hits), "sanctions_matches": hits, "source": source,
            "summary": f"{len(hits)} sanctions match(es) for {a.name} ({source})"}


class NewsArgs(BaseModel):
    vendor_name: str


def news_search(ctx: ToolContext, a: NewsArgs) -> dict:
    items = NEWS.get(a.vendor_name.strip().lower(), [])
    adverse = [i for i in items if i["sentiment"] == "adverse"]
    return {"news_items": items, "adverse_news": len(adverse), "source": "mock news search",
            "summary": f"{len(items)} news item(s), {len(adverse)} adverse"}


register(Tool("company_registry_lookup", "research",
              "Look up a company's legal registration status and incorporation date.",
              "low", RegistryArgs, company_registry_lookup))
register(Tool("sanctions_screen", "research",
              "Screen a company name against sanctions and watch lists.",
              "low", SanctionsArgs, sanctions_screen))
register(Tool("news_search", "research",
              "Search recent news about a company and flag adverse media.",
              "low", NewsArgs, news_search))
