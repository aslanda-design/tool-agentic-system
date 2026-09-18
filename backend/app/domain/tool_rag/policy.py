"""Selection policy — turning a fused ranking into an actual set of tools
to expose, per plans/tool_rag.md section 4.4. Four rules, applied in
order, all pure/no I/O so they're independently unit-testable without an
embedding model or a database:

1. Pinning is absolute — a caller-supplied set of tools (e.g. an agent's
   `terminal_tools`) is always included, regardless of retrieval score.
   Dropping a terminal tool would leave a task agent structurally unable
   to ever finish a run — this must never happen because of a retrieval
   miss.
2. Budget is token-aware, not a bare tool count — a tool with a large
   parameter schema costs more prompt tokens than one with none.
3. Per-server diversity is capped, so one verbose/self-similar server
   can't crowd out a single highly relevant tool from another.
4. A low top score, or a ceiling too small to bother ranking, skips
   retrieval altogether and signals the caller to fall back to exposing
   the entire ceiling — the same "deterministic fallback over an
   uncertain automated decision" instinct the rest of this app already
   applies (e.g. security_resolver's flag_for_review on any non-terminal
   outcome)."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.tool_rag.types import RetrievalCandidate, ToolKey


@dataclass(slots=True)
class SelectionPolicy:
    top_k: int
    max_schema_tokens: int
    min_score: float
    max_per_server: int
    skip_below_tool_count: int


@dataclass(slots=True)
class SelectionResult:
    selected: list[ToolKey]
    fallback_triggered: bool
    fallback_reason: str | None = None


def select(
    fused: list[RetrievalCandidate],
    ceiling_tool_count: int,
    pinned: set[ToolKey],
    policy: SelectionPolicy,
) -> SelectionResult:
    if ceiling_tool_count <= policy.skip_below_tool_count:
        return SelectionResult(
            selected=[],
            fallback_triggered=True,
            fallback_reason=(
                f"ceiling has only {ceiling_tool_count} tool(s) "
                f"(<= skip_below_tool_count={policy.skip_below_tool_count})"
            ),
        )

    if not fused or fused[0].score < policy.min_score:
        return SelectionResult(
            selected=[],
            fallback_triggered=True,
            fallback_reason="top fused score below TOOL_RAG_MIN_SCORE",
        )

    token_estimate = {c.key: c.token_estimate for c in fused}
    selected: list[ToolKey] = []
    used_tokens = 0
    per_server: dict[str, int] = {}

    # Rule 1 — pinning is absolute: included first, unconditionally, before
    # the budget/diversity rules below ever get a say.
    for key in pinned:
        selected.append(key)
        used_tokens += token_estimate.get(key, 0)
        per_server[key[0]] = per_server.get(key[0], 0) + 1

    for candidate in fused:
        if candidate.key in pinned:
            continue
        if len(selected) >= policy.top_k:
            break
        server = candidate.key[0]
        if per_server.get(server, 0) >= policy.max_per_server:  # Rule 3
            continue
        cost = candidate.token_estimate
        if used_tokens + cost > policy.max_schema_tokens:  # Rule 2
            continue
        selected.append(candidate.key)
        used_tokens += cost
        per_server[server] = per_server.get(server, 0) + 1

    return SelectionResult(selected=selected, fallback_triggered=False)
