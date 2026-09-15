"""MCP server exposing persisted (never live) market data to a tool-calling
agent over stdio. Registers tools only — every tool is a thin wrapper
defined in tools/, one function per file (see backend/ai/AGENTS.md). This
module must never write to stdout: stdio is the JSON-RPC transport, so even
our own logging goes to stderr (see logging.basicConfig below)."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ai.mcp_servers.market_data.tools.get_asset import get_asset
from ai.mcp_servers.market_data.tools.get_data_freshness import get_data_freshness
from ai.mcp_servers.market_data.tools.get_fx_rate import get_fx_rate
from ai.mcp_servers.market_data.tools.get_price_history import get_price_history

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("market-data")

mcp.tool()(get_asset)
mcp.tool()(get_price_history)
mcp.tool()(get_fx_rate)
mcp.tool()(get_data_freshness)
