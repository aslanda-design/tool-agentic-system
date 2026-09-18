"""Per-turn tool retrieval — see plans/tool_rag.md section 4. Called once
per agent turn from ai/common/tool_rag/retriever.py, itself called from an
agent's `_select_tools` (e.g. ai/agents/portfolio_assistant/agent.py, the
first caller). Retrieval only ever narrows within `all_tools_in_ceiling` —
the agent's own already-declared capability boundary (plans/tool_rag.md
section 4.6) — it is never a way for an agent to gain a tool its own code
didn't already authorize; that containment is enforced by
ToolIndexRepo.dense_search/lexical_search restricting their SQL to those
keys, not by filtering the result afterwards."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.tool_rag.fusion import reciprocal_rank_fusion
from app.domain.tool_rag.policy import SelectionPolicy
from app.domain.tool_rag.policy import select as apply_policy
from app.domain.tool_rag.types import RetrievalDecision, ToolKey
from app.ports.tool_rag import EmbeddingPort, ToolIndexRepo

CANDIDATES_PER_STAGE = 30


@dataclass(slots=True)
class RetrieveToolsUseCase:
    repo: ToolIndexRepo
    embedding_port: EmbeddingPort
    policy: SelectionPolicy

    def retrieve(
        self,
        query: str,
        pinned: set[ToolKey],
        all_tools_in_ceiling: set[ToolKey],
    ) -> RetrievalDecision:
        """`all_tools_in_ceiling` is resolved by the caller (ai/, since
        only a live MCP session knows what a `None` ceiling entry — "every
        tool this server currently has" — actually resolves to today; see
        ai/common/tool_rag/retriever.py::resolve_ceiling_tools)."""
        ceiling_count = len(all_tools_in_ceiling)

        query_embedding = self.embedding_port.embed([query])[0]
        dense = self.repo.dense_search(
            query_embedding, self.embedding_port.model_name, all_tools_in_ceiling, CANDIDATES_PER_STAGE
        )
        lexical = self.repo.lexical_search(query, all_tools_in_ceiling, CANDIDATES_PER_STAGE)
        fused = reciprocal_rank_fusion([dense, lexical])

        result = apply_policy(fused, ceiling_count, pinned, self.policy)
        selected_keys = list(all_tools_in_ceiling) if result.fallback_triggered else result.selected

        # Belt-and-suspenders containment check (dense/lexical_search
        # already restrict their SQL to all_tools_in_ceiling — this can
        # only ever be a no-op, and a regression test asserts it stays one;
        # see backend/tests/test_retrieve_tools_use_case.py).
        selected_keys = [key for key in selected_keys if key in all_tools_in_ceiling]

        selected: dict[str, list[str]] = {}
        for module, name in selected_keys:
            selected.setdefault(module, []).append(name)

        return RetrievalDecision(
            query=query,
            ceiling_tool_count=ceiling_count,
            dense_candidates=dense,
            lexical_candidates=lexical,
            fused=fused,
            selected=selected,
            fallback_triggered=result.fallback_triggered,
            fallback_reason=result.fallback_reason,
        )
