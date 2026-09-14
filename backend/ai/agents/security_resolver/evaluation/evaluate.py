"""Evaluate one or more models on the security_resolver agent's offline
case suite (plans/agentic_asset_mapping.md §6.6). The real model backend
IS called (this measures the model); Yahoo/OpenFIGI are never called and no
database is touched (ReplaySession stands in for the real MCP server).

    python -m ai.agents.security_resolver.evaluation.evaluate --model qwen3:8b
    python -m ai.agents.security_resolver.evaluation.evaluate --model qwen3:8b --model qwen3:14b --repeat 3

All models in one run share a provider (default: settings.agent_provider —
override with --provider); to compare across providers (e.g. a local
Ollama model against a Groq-hosted one) run this twice with different
--provider values and diff the two results files.

    python -m ai.agents.security_resolver.evaluation.evaluate --provider openai --model llama-3.3-70b-versatile

Results go to evaluation/results/<timestamp>.json (gitignored) plus a
Markdown summary table printed to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from ai.agents.security_resolver.agent import (
    AGENT_TOOLS,
    MCP_MODULE,
    PROMPT_PATH,
    TERMINAL_TOOLS,
    render_user_message,
)
from ai.agents.security_resolver.evaluation.replay import ReplaySession
from ai.common.agent_loop import run_agent
from ai.common.llm import build_chat_client
from ai.common.mcp_client import open_mcp_server
from app.config import settings

CASES_DIR = Path(__file__).with_name("cases")
LOCAL_CASES_DIR = Path(__file__).with_name("cases_local")
RESULTS_DIR = Path(__file__).with_name("results")

_SUMMARY_COLUMNS = [
    "model",
    "n",
    "final_accuracy",
    "safety_violations",
    "terminated_ok",
    "invalid_calls",
    "avg_tool_calls",
    "p50_latency_s",
    "p95_latency_s",
    "avg_prompt_tokens",
    "avg_completion_tokens",
]


def _load_cases() -> list[dict]:
    cases = []
    for directory in (CASES_DIR, LOCAL_CASES_DIR):
        if directory.exists():
            cases.extend(json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json")))
    return cases


async def _fetch_agent_tools() -> list[dict]:
    """The exact tool schemas production uses, fetched once from a real
    (otherwise unused this run) MCP server — so eval can never drift from
    what save_security_mapping/etc. actually accept as arguments."""
    async with open_mcp_server(MCP_MODULE, AGENT_TOOLS) as session:
        return await session.tool_schemas()


def _is_safety_violation(outcome_final_tool: str | None, outcome_symbol: str | None, expected: dict) -> bool:
    """A save that shouldn't have happened at all (expected a flag), or a
    save of the wrong candidate. A save the replay guard itself rejected
    (stale price / no currency) never reaches here — call_tool returns
    {"error": ...} and the loop doesn't treat it as terminal."""
    if outcome_final_tool != "save_security_mapping":
        return False
    if expected["final_tool"] != "save_security_mapping":
        return True
    return outcome_symbol != expected.get("symbol")


async def _run_one(case: dict, tools: list[dict], model: str, provider: str | None) -> dict:
    session = ReplaySession(case)
    chat_client = build_chat_client(provider=provider, model=model)
    started = time.monotonic()
    result = await run_agent(
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        user_message=render_user_message(session.get_resolution_dict()),
        tools=tools,
        call_tool=session.call_tool,
        chat_client=chat_client,
        max_steps=settings.agent_max_steps,
        terminal_tools=TERMINAL_TOOLS,
    )
    latency_s = time.monotonic() - started

    return {
        "case_id": case["id"],
        "model": model,
        "final_accuracy": session.outcome.final_tool == case["expected"]["final_tool"]
        and session.outcome.symbol == case["expected"].get("symbol"),
        "safety_violation": _is_safety_violation(session.outcome.final_tool, session.outcome.symbol, case["expected"]),
        "terminated_ok": result.status in TERMINAL_TOOLS.values(),
        "invalid_calls": sum(1 for c in result.tool_calls if (c["error"] or "").startswith("unknown tool")),
        "tool_calls": len(result.tool_calls),
        "latency_s": latency_s,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "status": result.status,
        "final_tool": session.outcome.final_tool,
        "final_symbol": session.outcome.symbol,
    }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def _summarize(rows: list[dict], model: str) -> dict:
    model_rows = [r for r in rows if r["model"] == model]
    n = len(model_rows)
    latencies = [r["latency_s"] for r in model_rows]
    return {
        "model": model,
        "n": n,
        "final_accuracy": sum(r["final_accuracy"] for r in model_rows) / n if n else 0.0,
        "safety_violations": sum(r["safety_violation"] for r in model_rows),
        "terminated_ok": sum(r["terminated_ok"] for r in model_rows) / n if n else 0.0,
        "invalid_calls": sum(r["invalid_calls"] for r in model_rows),
        "avg_tool_calls": statistics.fmean(r["tool_calls"] for r in model_rows) if n else 0.0,
        "p50_latency_s": _percentile(latencies, 0.5),
        "p95_latency_s": _percentile(latencies, 0.95),
        "avg_prompt_tokens": statistics.fmean(r["prompt_tokens"] for r in model_rows) if n else 0.0,
        "avg_completion_tokens": statistics.fmean(r["completion_tokens"] for r in model_rows) if n else 0.0,
    }


def _print_table(summaries: list[dict]) -> None:
    def cell(row: dict, col: str) -> str:
        value = row[col]
        return f"{value:.2f}" if isinstance(value, float) else str(value)

    print("| " + " | ".join(_SUMMARY_COLUMNS) + " |")
    print("|" + "---|" * len(_SUMMARY_COLUMNS))
    for summary in summaries:
        print("| " + " | ".join(cell(summary, col) for col in _SUMMARY_COLUMNS) + " |")


async def _main_async(models: list[str], repeat: int, provider: str | None) -> None:
    cases = _load_cases()
    if not cases:
        raise SystemExit(f"No cases found in {CASES_DIR} or {LOCAL_CASES_DIR}")
    tools = await _fetch_agent_tools()

    rows = [
        await _run_one(case, tools, model, provider)
        for model in models
        for case in cases
        for _ in range(repeat)
    ]

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    summaries = [_summarize(rows, model) for model in models]
    out_path = RESULTS_DIR / f"{stamp}.json"
    out_path.write_text(json.dumps({"rows": rows, "summaries": summaries}, indent=2), encoding="utf-8")

    _print_table(summaries)
    print(f"\nFull results: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", required=True, dest="models", help="A model tag/name; repeatable.")
    parser.add_argument(
        "--provider",
        default=None,
        choices=["ollama", "openai"],
        help="Override AGENT_PROVIDER for every model in this run (default: settings.agent_provider).",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case per model (default 1).")
    args = parser.parse_args()
    asyncio.run(_main_async(args.models, args.repeat, args.provider))


if __name__ == "__main__":
    main()
