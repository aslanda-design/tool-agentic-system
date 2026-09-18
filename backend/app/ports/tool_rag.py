"""Outbound ports for tool RAG (see plans/tool_rag.md).

`EmbeddingPort` wraps whichever backend turns text into vectors — the same
"provider is a config choice, not a code change" idea as
ai/common/llm.py's ChatClient, but living in app/ rather than ai/ because
its callers (RefreshToolIndexUseCase, RetrieveToolsUseCase) are app/ use
cases, and app/ must never import ai/ (backend/ai/AGENTS.md rule 1).

`ToolIndexRepo` is persistence for the `tool_index` table — dense
(pgvector) and lexical (pg_trgm) search live behind one interface so the
use cases above never see SQL, matching every other repo port in this
app."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from app.domain.tool_rag.types import RetrievalCandidate, ToolDocument, ToolKey


class EmbeddingPort(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Returns one vector per input text, same order. `model_name` is
        stored alongside every embedding it produces (see ToolIndexRepo)
        so a later switch to a different embedding model never compares
        incompatible vector spaces — retrieval only ever searches rows
        whose embedding_model matches the currently configured one."""
        ...

    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...


class ToolIndexRepo(ABC):
    @abstractmethod
    def get_content_hashes(self) -> dict[tuple[str, str, str, str], str]:
        """Existing (server_module, tool_name, doc_kind, doc_text) ->
        content_hash, for RefreshToolIndexUseCase to diff against — skips
        re-embedding rows whose content hasn't changed since the last
        build (plans/tool_rag.md section 3.3)."""

    @abstractmethod
    def upsert_documents(
        self,
        documents: list[ToolDocument],
        embeddings: list[list[float]],
        embedding_model: str,
        token_estimates: dict[ToolKey, int],
    ) -> None:
        """`documents`/`embeddings` are the same length and order (only the
        rows RefreshToolIndexUseCase determined actually changed)."""

    @abstractmethod
    def delete_missing(self, current_keys: set[tuple[str, str, str, str]]) -> int:
        """Remove rows for tools/examples no longer present in the live
        catalog (a tool removed from a server, an example query deleted
        from its examples file). Returns the number of rows deleted."""

    @abstractmethod
    def dense_search(
        self,
        query_embedding: list[float],
        embedding_model: str,
        candidate_keys: set[ToolKey] | None,
        limit: int,
    ) -> list[RetrievalCandidate]:
        """Cosine-similarity search, one row per tool (the best-scoring of
        its description/example-query rows — plans/tool_rag.md section
        3.1/3.4's "multi-vector document" collapse), restricted to
        `candidate_keys` when given. This restriction is the actual
        authorization boundary (plans/tool_rag.md section 4.6): it is
        enforced in the query itself, not filtered out afterwards, so
        retrieval can never surface a tool outside an agent's ceiling."""

    @abstractmethod
    def lexical_search(
        self, query_text: str, candidate_keys: set[ToolKey] | None, limit: int
    ) -> list[RetrievalCandidate]: ...


class RerankPort(Protocol):
    """Optional Stage 3 (plans/tool_rag.md section 4.3) — not implemented
    yet (Phase 4, deliberately deferred until the evaluation harness in
    Phase 5 shows hybrid retrieval alone isn't good enough). Declared here
    so RetrieveToolsUseCase's constructor shape doesn't need to change
    when it lands."""

    def rerank(self, query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]: ...
