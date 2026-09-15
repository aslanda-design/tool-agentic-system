"""Tests for ai/common/mcp_client.py's open_mcp_servers/MultiServerToolSession
— see plans/agentic_asset_mapping_phase7_8.md §3.2/§4. Spawns real stdio
subprocesses (tests/fixtures/dummy_server_{a,b,c}.py — tiny, pure, DB-free
MCP servers built only for this test) rather than mocking the MCP SDK, the
same "real protocol round trip" preference test_mcp_security_server.py's
protocol test already established. Slower than a pure-fake test (each
subprocess takes real wall-clock time to start) but this is exactly the
piece that's hardest to get right by inspection: which server a tool
dispatches to, and that a real name collision is actually caught."""

from __future__ import annotations

import asyncio

import pytest

from ai.common.mcp_client import open_mcp_servers

DUMMY_A = "tests.fixtures.dummy_server_a"
DUMMY_B = "tests.fixtures.dummy_server_b"
DUMMY_C = "tests.fixtures.dummy_server_c"


def test_tool_schemas_merges_across_servers_respecting_each_ones_filter():
    async def run():
        async with open_mcp_servers({DUMMY_A: {"tool_a"}, DUMMY_B: None}) as session:
            return await session.tool_schemas()

    schemas = asyncio.run(run())

    names = {s["function"]["name"] for s in schemas}
    # dummy_server_a exposes only tool_a (its "shared_name" tool is filtered
    # out by the {"tool_a"} allowlist); dummy_server_b has no filter (None),
    # so all of it — just tool_b — comes through.
    assert names == {"tool_a", "tool_b"}


def test_call_tool_dispatches_to_the_owning_server():
    async def run():
        async with open_mcp_servers({DUMMY_A: None, DUMMY_B: None}) as session:
            a = await session.call_tool("tool_a", {"x": 4})
            b = await session.call_tool("tool_b", {"x": 4})
            return a, b

    a_result, b_result = asyncio.run(run())

    assert a_result == 5
    assert b_result == 8


def test_call_tool_works_for_a_tool_not_exposed_to_the_model():
    """The whole point of an empty tool_names set (import_reviewer's
    pattern): the model never sees the tool in its schema, but the agent's
    own pre-fetch code can still call it directly."""

    async def run():
        async with open_mcp_servers({DUMMY_A: set()}) as session:
            schemas = await session.tool_schemas()
            result = await session.call_tool("tool_a", {"x": 10})
            return schemas, result

    schemas, result = asyncio.run(run())

    assert schemas == []
    assert result == 11


def test_call_tool_recovers_a_real_value_for_a_bare_dict_return_type():
    """Regression: a tool annotated `-> dict` (not `dict | None`) — the
    exact shape ai/mcp_servers/portfolio/tools/get_portfolio_summary.py and
    ai/mcp_servers/market_data/tools/get_data_freshness.py use — got
    `structured_content=None` from this SDK version despite returning real
    data, and call_tool used to return that None straight through,
    silently. Found via a live import_reviewer run (get_data_freshness()
    coming back None crashed render_user_message downstream), not by any
    test that mocked the MCP layer — a real round trip is the only thing
    that exercises the SDK's own structured_content behavior."""

    async def run():
        async with open_mcp_servers({DUMMY_A: None}) as session:
            return await session.call_tool("bare_dict_tool", {})

    result = asyncio.run(run())

    assert result == {"a": 1, "b": "two", "nested": {"c": [1, 2, 3]}}


def test_call_tool_returns_an_error_dict_for_a_name_no_server_has():
    async def run():
        async with open_mcp_servers({DUMMY_A: None}) as session:
            return await session.call_tool("does_not_exist", {})

    result = asyncio.run(run())

    assert "error" in result


def _find_value_error(exc: BaseException) -> ValueError | None:
    """Python 3.11+ wraps an exception raised while an AsyncExitStack is
    unwinding already-entered subprocess contexts in an ExceptionGroup —
    the ValueError from open_mcp_servers' collision check is still in
    there, just not the top-level exception pytest.raises sees directly."""
    if isinstance(exc, ValueError):
        return exc
    for sub in getattr(exc, "exceptions", ()):
        found = _find_value_error(sub)
        if found is not None:
            return found
    return None


def test_open_mcp_servers_raises_on_a_real_tool_name_collision():
    async def run():
        async with open_mcp_servers({DUMMY_A: None, DUMMY_C: None}):
            pass  # dummy_server_a and dummy_server_c both define "shared_name"

    with pytest.raises(BaseException) as exc_info:
        asyncio.run(run())

    found = _find_value_error(exc_info.value)
    assert found is not None, f"no ValueError found in {exc_info.value!r}"
    assert "shared_name" in str(found)
