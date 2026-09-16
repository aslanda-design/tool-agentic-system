"""MCP server exposing the Quant Lab's models to a tool-calling agent over
stdio — read-only/advisory only (see plans/quant_lab.md section 0.2): an
agent can list models, get a deterministic recommendation, and explain a
run the user already made, but cannot trigger a simulation itself.
Registers tools only — every tool is a thin wrapper defined in tools/, one
function per file (see backend/ai/AGENTS.md). This module must never write
to stdout: stdio is the JSON-RPC transport, so even our own logging goes to
stderr (see logging.basicConfig below)."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ai.mcp_servers.quant.tools.explain_run import explain_run
from ai.mcp_servers.quant.tools.list_models import list_models
from ai.mcp_servers.quant.tools.recommend_model import recommend_model

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("quant")

mcp.tool()(list_models)
mcp.tool()(recommend_model)
mcp.tool()(explain_run)
