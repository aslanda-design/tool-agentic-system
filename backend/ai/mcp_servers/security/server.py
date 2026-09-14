"""FastMCP server exposing the security resolver to a tool-calling agent
over stdio. Registers tools only — every tool is a thin wrapper defined in
tools/, one function per file (see backend/ai/AGENTS.md). This module must
never write to stdout: stdio is the JSON-RPC transport, so even our own
logging goes to stderr (see logging.basicConfig below)."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ai.mcp_servers.security.tools.add_candidate import add_candidate
from ai.mcp_servers.security.tools.flag_for_review import flag_for_review
from ai.mcp_servers.security.tools.get_resolution import get_resolution
from ai.mcp_servers.security.tools.list_pending_resolutions import list_pending_resolutions
from ai.mcp_servers.security.tools.lookup_isin import lookup_isin
from ai.mcp_servers.security.tools.resolve_isin import resolve_isin
from ai.mcp_servers.security.tools.save_security_mapping import save_security_mapping
from ai.mcp_servers.security.tools.search_listings import search_listings
from ai.mcp_servers.security.tools.validate_listing import validate_listing

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("security-resolver")

mcp.tool()(list_pending_resolutions)
mcp.tool()(get_resolution)
mcp.tool()(resolve_isin)
mcp.tool()(lookup_isin)
mcp.tool()(search_listings)
mcp.tool()(validate_listing)
mcp.tool()(add_candidate)
mcp.tool()(save_security_mapping)
mcp.tool()(flag_for_review)
