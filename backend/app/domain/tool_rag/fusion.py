"""Reciprocal Rank Fusion (RRF) — see plans/tool_rag.md section 4.2 for why
ranks, not raw scores, are fused: cosine similarity (dense) and trigram
similarity (lexical) live on different, non-comparable scales, so summing
raw scores would silently let whichever stage happens to produce larger
numbers dominate. Fusing by rank sidesteps that."""

from __future__ import annotations

from app.domain.tool_rag.types import RetrievalCandidate, ToolKey

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievalCandidate]], k: int = DEFAULT_RRF_K
) -> list[RetrievalCandidate]:
    """score(tool) = sum, over every list containing it, of 1 / (k + rank),
    rank starting at 1 within that list. A tool ranked #1 in one list and
    absent from the other still surfaces near the top; a tool ranked deep
    in both doesn't beat one ranked highly in either. k=60 is the standard
    default from the original RRF paper (Cormack, Clarke & Buettcher,
    2009) — large enough that rank 1 vs. rank 2 isn't wildly more
    important than rank 30 vs. rank 31.

    Each input list is already assumed to be one tool per key (dense/
    lexical search collapse a tool's several document rows to its
    best-scoring one before this is called — see
    app/adapters/persistence/repositories.py::SqlToolIndexRepo). `doc_text`
    and `token_estimate` are carried through from whichever list first
    contributes a given key, for the selection policy to use downstream."""
    scores: dict[ToolKey, float] = {}
    doc_text: dict[ToolKey, str] = {}
    token_estimate: dict[ToolKey, int] = {}

    for ranked in ranked_lists:
        for rank, candidate in enumerate(ranked, start=1):
            scores[candidate.key] = scores.get(candidate.key, 0.0) + 1.0 / (k + rank)
            doc_text.setdefault(candidate.key, candidate.doc_text)
            token_estimate.setdefault(candidate.key, candidate.token_estimate)

    fused = [
        RetrievalCandidate(key=key, score=score, doc_text=doc_text[key], token_estimate=token_estimate[key])
        for key, score in scores.items()
    ]
    fused.sort(key=lambda c: c.score, reverse=True)
    return fused
