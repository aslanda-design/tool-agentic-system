"""CLI to (re)build the tool RAG index — see plans/tool_rag.md section 3.6.

Deliberately NOT wired into FastAPI's lifespan or any request path. The
same incident backend/ai/AGENTS.md documents for the security resolver
(wiring resolution into `lifespan`/`BackgroundTasks` made every `pytest`
run make real OpenFIGI/Yahoo calls, because Starlette's TestClient runs
both synchronously) applies here too: spawning six MCP subprocesses and
calling an embedding model on every backend startup, including every test
run, would be the same class of mistake. This is a deliberate, explicit
action a developer runs after adding or changing a tool:

    python -m ai.common.tool_rag.build_index
    python -m ai.common.tool_rag.build_index --server ai.mcp_servers.quant

Same shape as app/application/backfill_quant_history.py's standalone-
script pattern — not a route, not a startup hook."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ai.common.tool_rag.catalog import enumerate_catalog
from app.adapters.persistence.session import SessionLocal
from app.container import build_refresh_tool_index_use_case

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)


async def main(servers: list[str] | None) -> None:
    raw_tools = await enumerate_catalog(servers)
    db = SessionLocal()
    try:
        result = build_refresh_tool_index_use_case(db).refresh(raw_tools)
        db.commit()
    finally:
        db.close()
    logger.info(
        "tool index refresh: %d tool(s), %d document(s), %d embedded, %d unchanged, %d deleted",
        result["tools"],
        result["documents"],
        result["embedded"],
        result["skipped"],
        result["deleted"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server", action="append", dest="servers", help="Limit to one server module (repeatable)"
    )
    args = parser.parse_args()
    asyncio.run(main(args.servers))
