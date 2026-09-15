"""A tiny, real (but pure/DB-free) MCP server used only to test
ai/common/mcp_client.py's open_mcp_servers's tool-name collision detection
— see test_mcp_client_multi.py. Deliberately defines a tool with the same
name as tests/fixtures/dummy_server_a.py. Not part of the app; never
imported outside tests/."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("dummy-c")


@mcp.tool()
def shared_name() -> str:
    """Returns which dummy server answered."""
    return "c"


if __name__ == "__main__":
    mcp.run()
