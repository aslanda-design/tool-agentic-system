"""MCP server exposing external news/filings lookups to a tool-calling
agent over stdio — see plans/market_intel.md. Registers tools only — every
tool is a thin wrapper defined in tools/, one function per file (see
backend/ai/AGENTS.md). This module must never write to stdout: stdio is the
JSON-RPC transport, so even our own logging goes to stderr (see
logging.basicConfig below).

Every tool here makes an outbound network call — see backend/ai/AGENTS.md
rule 13 for the egress guard this requires of any tool added to this
server: a bare symbol/topic argument, never an asset_id, never a batch."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ai.mcp_servers.market_intel.tools.get_asset_news import get_asset_news
from ai.mcp_servers.market_intel.tools.get_asset_sentiment import get_asset_sentiment
from ai.mcp_servers.market_intel.tools.get_filing_events import get_filing_events
from ai.mcp_servers.market_intel.tools.get_macro_headlines import get_macro_headlines

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("market-intel")

mcp.tool()(get_asset_news)
mcp.tool()(get_filing_events)
mcp.tool()(get_asset_sentiment)
mcp.tool()(get_macro_headlines)
