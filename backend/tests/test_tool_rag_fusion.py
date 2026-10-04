"""reciprocal_rank_fusion — see plans/tool_rag.md section 4.2."""

from __future__ import annotations

from app.domain.tool_rag.fusion import reciprocal_rank_fusion
from app.domain.tool_rag.types import RetrievalCandidate


def _cand(
    module: str, name: str, score: float, doc_text: str = "", token_estimate: int = 0
) -> RetrievalCandidate:
    return RetrievalCandidate(
        key=(module, name), score=score, doc_text=doc_text, token_estimate=token_estimate
    )


def test_a_tool_ranked_first_in_one_list_and_absent_from_the_other_still_surfaces_near_the_top():
    dense = [_cand("m", "a", 0.9), _cand("m", "b", 0.5)]
    lexical = [_cand("m", "c", 0.8)]  # "a" never matched lexically at all

    fused = reciprocal_rank_fusion([dense, lexical])

    assert fused[0].key == ("m", "a")


def test_a_tool_present_in_both_lists_outranks_one_present_in_only_one():
    dense = [_cand("m", "a", 0.9), _cand("m", "b", 0.1)]
    lexical = [_cand("m", "b", 0.9), _cand("m", "a", 0.1)]

    fused = reciprocal_rank_fusion([dense, lexical])

    # "a" and "b" both rank #1 in one list and #2 in the other — tie, but
    # both should outrank a tool that only ever appears once.
    only_once = [_cand("m", "c", 0.99)]
    fused_with_c = reciprocal_rank_fusion([dense, lexical, only_once])
    top_two = {c.key for c in fused_with_c[:2]}
    assert top_two == {("m", "a"), ("m", "b")}
    assert fused[0].score == fused[1].score  # exact tie by symmetry


def test_doc_text_and_token_estimate_are_carried_through():
    dense = [_cand("m", "a", 0.9, doc_text="does a thing", token_estimate=42)]

    fused = reciprocal_rank_fusion([dense, []])

    assert fused[0].doc_text == "does a thing"
    assert fused[0].token_estimate == 42


def test_empty_input_returns_empty_output():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
