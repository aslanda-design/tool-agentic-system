"""Entry point: `python -m ai.mcp_servers.analytics` runs the server over
stdio. See server.py for tool registration."""

from __future__ import annotations

from ai.mcp_servers.analytics.server import mcp

if __name__ == "__main__":
    mcp.run()
