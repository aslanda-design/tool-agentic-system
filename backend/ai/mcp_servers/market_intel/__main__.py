"""Entry point: `python -m ai.mcp_servers.market_intel` runs the server
over stdio. See server.py for tool registration."""

from __future__ import annotations

from ai.mcp_servers.market_intel.server import mcp

if __name__ == "__main__":
    mcp.run()
