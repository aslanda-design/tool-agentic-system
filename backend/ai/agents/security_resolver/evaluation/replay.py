"""Deterministic, offline replay of the `security` MCP server's tools, for
agent evaluation (plans/agentic_asset_mapping.md §6.6). Loads one case (see
cases/*.json for the shape) and exposes an async `call_tool(name, arguments)`
with the exact same contract as `ai.common.mcp_client.McpToolSession.call_tool`
— `ai.common.agent_loop.run_agent` can't tell the difference. Neither Yahoo
nor OpenFIGI is ever called: every fact a tool can return is baked into the
case file ahead of time, so every model sees exactly the same world."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ai.common.jsonable import to_jsonable
from app.domain.listing_scoring import score_candidates
from app.domain.listings import Candidate, ListingInfo, ResolutionContext


def _listing_info_from_dict(data: dict | None) -> ListingInfo | None:
    if data is None:
        return None
    return ListingInfo(
        symbol=data["symbol"],
        name=data["name"],
        currency=data.get("currency"),
        quote_type=data.get("quote_type"),
        last_close=Decimal(str(data["last_close"])) if data.get("last_close") is not None else None,
        last_trade_date=date.fromisoformat(data["last_trade_date"]) if data.get("last_trade_date") else None,
        avg_volume=Decimal(str(data["avg_volume"])) if data.get("avg_volume") is not None else None,
    )


def _candidate_from_dict(data: dict) -> Candidate:
    return Candidate(
        symbol=data["symbol"],
        found_by=set(data.get("found_by") or []),
        info=_listing_info_from_dict(data.get("info")),
        mic=data.get("mic"),
        asset_class=data.get("asset_class"),
        features=dict(data.get("features") or {}),
        score=data.get("score", 0),
        id=data.get("id"),
    )


@dataclass(slots=True)
class ReplayOutcome:
    """What the agent actually decided during one replayed run — compared
    against a case's `expected` by evaluate.py."""

    final_tool: str | None = None
    symbol: str | None = None
    reason: str | None = None


class ReplaySession:
    """One case's fixed world, replayed for one agent run. A fresh instance
    per run — `candidates`/`outcome` are mutated as the agent calls tools."""

    def __init__(self, case: dict) -> None:
        self.case = case
        self.resolution = case["resolution"]
        self.ctx = ResolutionContext(**self.resolution["context"])
        self.candidates: list[Candidate] = [_candidate_from_dict(c) for c in self.resolution["candidates"]]
        self._next_candidate_id = max((c.id or 0 for c in self.candidates), default=0) + 1
        self.fixtures: dict[str, dict] = case.get("tool_fixtures", {})
        self.outcome = ReplayOutcome()

    def get_resolution_dict(self) -> dict:
        """A get_resolution-shaped dict for the first user message — see
        ai.agents.security_resolver.agent.render_user_message."""
        return copy.deepcopy(self.resolution)

    async def call_tool(self, name: str, arguments: dict) -> Any:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        return handler(**arguments)

    # --- read tools: look up the fixture by the argument the real tool keys on ---

    def _tool_validate_listing(self, symbol: str) -> dict | None:
        return self.fixtures.get("validate_listing", {}).get(symbol)

    def _tool_search_listings(self, query: str) -> list[dict]:
        return self.fixtures.get("search_listings", {}).get(query, [])

    def _tool_lookup_isin(self, isin: str) -> list[dict] | None:
        return self.fixtures.get("lookup_isin", {}).get(isin)

    # --- add_candidate: rescore with the real scorer against fixture data ---

    def _tool_add_candidate(self, resolution_id: int, symbol: str) -> dict:
        if any(c.symbol == symbol for c in self.candidates):
            return {"error": f"{symbol!r} is already a candidate on this resolution"}
        info = _listing_info_from_dict(self._tool_validate_listing(symbol))
        candidate = Candidate(symbol=symbol, found_by={"agent"}, info=info, id=self._next_candidate_id)
        self._next_candidate_id += 1
        score_candidates(self.ctx, [*self.candidates, candidate], today=date.today())
        self.candidates.append(candidate)
        return to_jsonable(candidate)

    # --- terminal tools: enforce the same guards as accept()/flag_for_review() ---

    def _tool_save_security_mapping(self, resolution_id: int, candidate_id: int, reason: str) -> dict:
        candidate = next((c for c in self.candidates if c.id == candidate_id), None)
        if candidate is None:
            return {"error": f"candidate {candidate_id} does not belong to resolution {resolution_id}"}
        if not candidate.features.get("has_recent_price"):
            return {"error": f"{candidate.symbol!r} has no recent price — can't be accepted"}
        if candidate.info is None or candidate.info.currency is None:
            return {"error": f"{candidate.symbol!r} has no currency — can't be accepted"}
        self.outcome = ReplayOutcome(final_tool="save_security_mapping", symbol=candidate.symbol, reason=reason)
        return {"status": "RESOLVED_BY_AGENT"}

    def _tool_flag_for_review(self, resolution_id: int, reason: str) -> dict:
        self.outcome = ReplayOutcome(final_tool="flag_for_review", symbol=None, reason=reason)
        return {"status": "NEEDS_REVIEW"}
