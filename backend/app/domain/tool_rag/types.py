"""Domain types for tool RAG (see plans/tool_rag.md). Pure Python, no I/O —
same shape as app/domain/quant/types.py: this package holds only data and
pure functions; embedding calls and persistence are ports
(app/ports/tool_rag.py), never called from here.

`ToolKey` is `(server_module, tool_name)` — the same pair every agent's
SERVERS/CEILING dict and ai.common.mcp_client already key on, so a
retrieval result maps straight back onto that shape with no translation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ToolKey = tuple[str, str]


@dataclass(slots=True)
class ToolDocument:
    """One embeddable unit for a tool — either its own MCP description or
    one hand-authored example query (plans/tool_rag.md section 3.1's
    "multi-vector document" design: a tool with N example queries produces
    N+1 rows in the index, all sharing (server_module, tool_name), and
    retrieval takes the best-scoring row per tool, not per row)."""

    server_module: str
    tool_name: str
    doc_kind: Literal["description", "example_query"]
    doc_text: str
    category: str
    content_hash: str


@dataclass(slots=True)
class RetrievalCandidate:
    """One tool's best score from one retrieval stage (dense, lexical, or
    fused). `token_estimate` rides along so the selection policy (§4.4) can
    budget without a second lookup — populated from the tool's live JSON
    schema size at index time (ai/common/tool_rag/catalog.py), not from
    doc_text length."""

    key: ToolKey
    score: float
    doc_text: str = ""
    token_estimate: int = 0


@dataclass(slots=True)
class RetrievalDecision:
    """One turn's full retrieval trace — persisted verbatim into
    agent_runs.tool_retrieval (plans/tool_rag.md section 5) for audit and
    evaluation. `selected` is already grouped into the {module: [tool_names]}
    shape ai.common.mcp_client.open_mcp_servers expects."""

    query: str
    ceiling_tool_count: int
    dense_candidates: list[RetrievalCandidate] = field(default_factory=list)
    lexical_candidates: list[RetrievalCandidate] = field(default_factory=list)
    fused: list[RetrievalCandidate] = field(default_factory=list)
    selected: dict[str, list[str]] = field(default_factory=dict)
    fallback_triggered: bool = False
    fallback_reason: str | None = None
