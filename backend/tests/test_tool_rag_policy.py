"""Selection policy — see plans/tool_rag.md section 4.4. Each rule
(pinning, budget, diversity, fallback) gets its own test, using fixtures
small enough to reason about by hand."""

from __future__ import annotations

from app.domain.tool_rag.policy import SelectionPolicy, select
from app.domain.tool_rag.types import RetrievalCandidate

DEFAULT_POLICY = SelectionPolicy(
    top_k=8, max_schema_tokens=10_000, min_score=0.0, max_per_server=10, skip_below_tool_count=0
)


def _cand(module: str, name: str, score: float, token_estimate: int = 10) -> RetrievalCandidate:
    return RetrievalCandidate(key=(module, name), score=score, token_estimate=token_estimate)


def test_pinned_tools_are_always_selected_even_with_the_worst_possible_score():
    fused = [_cand("m", "good", 0.9)]
    pinned = {("m", "terminal_tool")}  # not even in the fused ranking at all

    result = select(fused, ceiling_tool_count=5, pinned=pinned, policy=DEFAULT_POLICY)

    assert ("m", "terminal_tool") in result.selected
    assert not result.fallback_triggered


def test_pinning_is_not_affected_by_the_top_k_limit():
    policy = SelectionPolicy(
        top_k=1, max_schema_tokens=10_000, min_score=0.0, max_per_server=10, skip_below_tool_count=0
    )
    fused = [_cand("m", "a", 0.9), _cand("m", "b", 0.8)]
    pinned = {("m", "pin1"), ("m", "pin2")}

    result = select(fused, ceiling_tool_count=5, pinned=pinned, policy=policy)

    assert pinned.issubset(set(result.selected))


def test_budget_stops_adding_tools_once_the_token_cap_is_reached():
    policy = SelectionPolicy(
        top_k=10, max_schema_tokens=25, min_score=0.0, max_per_server=10, skip_below_tool_count=0
    )
    fused = [_cand("m", "a", 0.9, token_estimate=20), _cand("m", "b", 0.8, token_estimate=20)]

    result = select(fused, ceiling_tool_count=5, pinned=set(), policy=policy)

    assert result.selected == [("m", "a")]  # "b" would push total to 40 > 25


def test_per_server_diversity_cap_is_enforced():
    policy = SelectionPolicy(
        top_k=10, max_schema_tokens=10_000, min_score=0.0, max_per_server=1, skip_below_tool_count=0
    )
    fused = [_cand("server_a", "1", 0.9), _cand("server_a", "2", 0.8), _cand("server_b", "1", 0.7)]

    result = select(fused, ceiling_tool_count=5, pinned=set(), policy=policy)

    assert result.selected == [("server_a", "1"), ("server_b", "1")]


def test_a_low_top_score_triggers_fallback_with_no_selection():
    policy = SelectionPolicy(
        top_k=8, max_schema_tokens=10_000, min_score=0.5, max_per_server=10, skip_below_tool_count=0
    )
    fused = [_cand("m", "a", 0.2)]

    result = select(fused, ceiling_tool_count=5, pinned=set(), policy=policy)

    assert result.fallback_triggered
    assert result.selected == []
    assert result.fallback_reason is not None


def test_a_small_ceiling_triggers_fallback_regardless_of_score():
    policy = SelectionPolicy(
        top_k=8,
        max_schema_tokens=10_000,
        min_score=0.0,
        max_per_server=10,
        skip_below_tool_count=12,
    )
    fused = [_cand("m", "a", 0.99)]  # a great match — still shouldn't matter

    result = select(fused, ceiling_tool_count=5, pinned=set(), policy=policy)

    assert result.fallback_triggered
    assert "ceiling" in result.fallback_reason


def test_empty_fused_list_triggers_fallback():
    result = select([], ceiling_tool_count=20, pinned=set(), policy=DEFAULT_POLICY)

    assert result.fallback_triggered
