"""MCP server exposing read-only portfolio analytics to a tool-calling
agent over stdio. Registers tools only — every tool is a thin wrapper
defined in tools/, one function per file (see backend/ai/AGENTS.md). This
module must never write to stdout: stdio is the JSON-RPC transport, so even
our own logging goes to stderr (see logging.basicConfig below)."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ai.mcp_servers.analytics.tools.check_import_prices import check_import_prices
from ai.mcp_servers.analytics.tools.get_concentration import get_concentration
from ai.mcp_servers.analytics.tools.get_currency_exposure import get_currency_exposure
from ai.mcp_servers.analytics.tools.get_drawdown import get_drawdown
from ai.mcp_servers.analytics.tools.get_returns import get_returns

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("analytics")

mcp.tool()(get_returns)
mcp.tool()(get_concentration)
mcp.tool()(get_currency_exposure)
mcp.tool()(get_drawdown)
mcp.tool()(check_import_prices)
