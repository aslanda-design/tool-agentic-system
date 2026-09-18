"""Builds/refreshes the tool_index table from a live catalog snapshot — see
plans/tool_rag.md section 3. Never called from a request path or app
startup: the same incident backend/ai/AGENTS.md documents for the security
resolver (a TestClient's `lifespan`/`BackgroundTasks` making real network
calls as a side effect of something that looked unrelated) applies here
too — spawning MCP subprocesses and calling an embedding model on every
backend startup, including every test run, would be the same mistake. The
only caller is ai/common/tool_rag/build_index.py, an explicit CLI."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.domain.tool_rag.types import ToolDocument
from app.ports.tool_rag import EmbeddingPort, ToolIndexRepo


@dataclass(slots=True)
class RawTool:
    """One tool as enumerated live from a running MCP server, plus the
    curated metadata ai/common/tool_rag/examples/*.json adds — see
    ai/common/tool_rag/catalog.py, the only builder of these. Lives in
    application/ (not domain/) because it's an input shape for this use
    case, not a persisted or scored domain concept."""

    server_module: str
    tool_name: str
    description: str
    example_queries: list[str]
    category: str
    schema_token_estimate: int


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RefreshToolIndexUseCase:
    def __init__(self, repo: ToolIndexRepo, embedding_port: EmbeddingPort) -> None:
        self.repo = repo
        self.embedding_port = embedding_port

    def refresh(self, raw_tools: list[RawTool]) -> dict:
        """Returns a small summary dict (tool/document/embedded/skipped/
        deleted counts) for the CLI to log — see
        ai/common/tool_rag/build_index.py."""
        documents: list[ToolDocument] = []
        for tool in raw_tools:
            documents.append(
                ToolDocument(
                    server_module=tool.server_module,
                    tool_name=tool.tool_name,
                    doc_kind="description",
                    doc_text=tool.description,
                    category=tool.category,
                    content_hash=_hash(tool.description),
                )
            )
            for query in tool.example_queries:
                documents.append(
                    ToolDocument(
                        server_module=tool.server_module,
                        tool_name=tool.tool_name,
                        doc_kind="example_query",
                        doc_text=query,
                        category=tool.category,
                        content_hash=_hash(query),
                    )
                )

        existing_hashes = self.repo.get_content_hashes()
        changed = [
            d
            for d in documents
            if existing_hashes.get((d.server_module, d.tool_name, d.doc_kind, d.doc_text)) != d.content_hash
        ]
        skipped = len(documents) - len(changed)

        if changed:
            embeddings = self.embedding_port.embed([d.doc_text for d in changed])
            token_estimates = {(t.server_module, t.tool_name): t.schema_token_estimate for t in raw_tools}
            self.repo.upsert_documents(changed, embeddings, self.embedding_port.model_name, token_estimates)

        current_keys = {(d.server_module, d.tool_name, d.doc_kind, d.doc_text) for d in documents}
        deleted = self.repo.delete_missing(current_keys)

        return {
            "tools": len(raw_tools),
            "documents": len(documents),
            "embedded": len(changed),
            "skipped": skipped,
            "deleted": deleted,
        }
