"""MCP server exposing read-only portfolio queries to a tool-calling agent
over stdio. Registers tools only — every tool is a thin wrapper defined in
tools/, one function per file (see backend/ai/AGENTS.md). This module must
never write to stdout: stdio is the JSON-RPC transport, so even our own
logging goes to stderr (see logging.basicConfig below)."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ai.mcp_servers.portfolio.tools.get_allocation import get_allocation
from ai.mcp_servers.portfolio.tools.get_portfolio_summary import get_portfolio_summary
from ai.mcp_servers.portfolio.tools.get_value_history import get_value_history
from ai.mcp_servers.portfolio.tools.list_accounts import list_accounts
from ai.mcp_servers.portfolio.tools.list_positions import list_positions
from ai.mcp_servers.portfolio.tools.list_transactions import list_transactions

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("portfolio")

mcp.tool()(get_portfolio_summary)
mcp.tool()(list_positions)
mcp.tool()(get_allocation)
mcp.tool()(get_value_history)
mcp.tool()(list_transactions)
mcp.tool()(list_accounts)
