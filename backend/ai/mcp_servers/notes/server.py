"""MCP server exposing agent-written notes to a tool-calling agent over
stdio — the one write path outside `security` (see backend/ai/AGENTS.md
rule 6). Registers tools only — every tool is a thin wrapper defined in
tools/, one function per file (see backend/ai/AGENTS.md). This module must
never write to stdout: stdio is the JSON-RPC transport, so even our own
logging goes to stderr (see logging.basicConfig below)."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ai.mcp_servers.notes.tools.list_notes import list_notes
from ai.mcp_servers.notes.tools.save_note import save_note

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("notes")

mcp.tool()(save_note)
mcp.tool()(list_notes)
