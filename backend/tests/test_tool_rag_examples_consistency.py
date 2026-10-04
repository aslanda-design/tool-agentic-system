"""Enforces backend/ai/AGENTS.md's tool RAG rule: every MCP tool ships with
example_queries in ai/common/tool_rag/examples/<server>.json (see
plans/tool_rag.md sections 3.1/3.2), and every examples entry names a tool
that actually still exists. This is the automated half of that rule —
ai/common/tool_rag/catalog.py enforces the second half (no stale entries)
again at index-build time against the *live* MCP schema, which this test
can't reach without a real subprocess round trip; checking against the
tool files on disk here is the fast, no-subprocess approximation."""

from __future__ import annotations

import json
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[1] / "ai"
EXAMPLES_DIR = AI_DIR / "common" / "tool_rag" / "examples"
MCP_SERVERS_DIR = AI_DIR / "mcp_servers"

SERVERS = ["security", "portfolio", "market_data", "analytics", "notes", "quant", "market_intel"]


def _tool_files(server: str) -> set[str]:
    tools_dir = MCP_SERVERS_DIR / server / "tools"
    return {p.stem for p in tools_dir.glob("*.py") if p.stem != "__init__"}


def _examples(server: str) -> dict:
    path = EXAMPLES_DIR / f"{server}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_every_examples_entry_matches_a_real_tool_file():
    for server in SERVERS:
        known = _tool_files(server)
        unknown = set(_examples(server)) - known
        assert not unknown, f"{server}.json references unknown tool(s) {unknown} — stale entry?"


def test_every_tool_has_at_least_three_example_queries():
    for server in SERVERS:
        known = _tool_files(server)
        examples = _examples(server)
        missing = known - set(examples)
        assert not missing, f"{server}: tool(s) {missing} have no example_queries in {server}.json"
        for tool, meta in examples.items():
            count = len(meta.get("example_queries", []))
            assert count >= 3, f"{server}.{tool}: has {count} example_queries, needs >= 3"
