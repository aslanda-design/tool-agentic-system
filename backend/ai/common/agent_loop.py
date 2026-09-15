"""The one tool-calling loop every agent under ai/agents/ uses — a plain
`for` loop around an injected ChatClient and an injected call_tool, no
agent framework (see backend/ai/AGENTS.md rule 9). Model-, provider-, and
MCP-server-agnostic: an agent supplies its own system prompt, tool list,
call_tool function, chat client, and which tool names end the run (and with
what status) — see ai.common.llm.build_chat_client for how a concrete
ChatClient (Ollama, or any OpenAI-compatible API) gets built."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai.common.llm import ChatClient

CallTool = Callable[[str, dict], Awaitable[Any]]


@dataclass(slots=True)
class AgentRunResult:
    status: str  # caller-defined (e.g. 'SAVED'/'FLAGGED'), or 'MAX_STEPS'/'ERROR'
    steps: int
    tool_calls: list[dict] = field(default_factory=list)
    final_message: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    error: str | None = None


def _is_error(output: Any) -> bool:
    return isinstance(output, dict) and "error" in output


async def run_agent(
    system_prompt: str,
    user_message: str,
    tools: list[dict],
    call_tool: CallTool,
    chat_client: ChatClient,
    max_steps: int,
    terminal_tools: dict[str, str] | None = None,
    history: list[dict] | None = None,
) -> AgentRunResult:
    """Run one tool-calling conversation to completion.

    Two ways to end depending on `terminal_tools`:
    - **Given** (a dict — every agent before portfolio_assistant): stops as
      soon as a tool named in `terminal_tools` is called and succeeds (its
      result isn't a `{"error": ...}` dict), returning that tool's mapped
      status. If the model replies without calling any tool, it's nudged
      once to finish with a terminal tool; a second text-only reply ends
      the run as ERROR.
    - **`None`** (conversational agents — e.g. portfolio_assistant): the
      model's first plain-text reply (no tool call) ends the run
      immediately with status `"REPLIED"` and that text as
      `final_message` — no nudge, since a plain-text answer is the whole
      point for a chat agent, not a failure to finish.

    Either way, runs at most `max_steps` model turns, ending MAX_STEPS if
    none of them terminate the run. Does NOT enforce a wall-clock timeout —
    wrap the call in `asyncio.wait_for` for that (the caller decides the
    budget, e.g. `settings.agent_timeout_seconds`).

    Args:
        system_prompt: Fixed instructions (see e.g.
            ai/agents/security_resolver/prompt.md).
        user_message: The one piece of per-run context (e.g. a rendered
            resolution, or the user's new chat message) — put here rather
            than fetched via a tool call, so small models can't skip
            reading it.
        tools: Tool schemas in OpenAI/Groq function-calling shape (see
            ai.common.mcp_client.McpToolSession.tool_schemas).
        call_tool: `async (name, arguments) -> result` — the real MCP
            session in production, a fixture-backed replay function in
            evaluation (see ai/agents/*/evaluation/replay.py).
        chat_client: Anything with a `.chat(messages, tools) -> dict`
            method (see ai.common.llm.ChatClient/build_chat_client) — which
            model and provider (Ollama, Groq, ...) is entirely this
            object's concern, not the loop's.
        max_steps: Maximum number of model turns before giving up.
        terminal_tools: Maps a tool name that ends the run to the status to
            report when it succeeds, e.g.
            `{"save_security_mapping": "SAVED", "flag_for_review": "FLAGGED"}`,
            or `None` for the conversational ending above.
        history: Prior turns to replay before `user_message`, as plain
            `{"role": "user"|"assistant", "content": str}` dicts — no tool
            calls or tool results, only each past turn's final text
            (portfolio_assistant's session memory: replaying stale tool
            *results* from turns ago risks answering from outdated
            portfolio data, so a past turn's own conclusion is all that
            gets replayed; the model re-calls a tool if it needs current
            numbers). `None`/`[]` for a fresh run (every agent before
            portfolio_assistant).
    """
    started = time.monotonic()
    allowed_names = {t["function"]["name"] for t in tools}
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    calls: list[dict] = []
    prompt_tokens = completion_tokens = 0
    nudged = False
    step = 0

    def finish(status: str, final_message: str = "", error: str | None = None) -> AgentRunResult:
        return AgentRunResult(
            status=status,
            steps=step,
            tool_calls=calls,
            final_message=final_message,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=error,
        )

    for step in range(1, max_steps + 1):
        reply = chat_client.chat(messages, tools)
        prompt_tokens += reply["prompt_tokens"]
        completion_tokens += reply["completion_tokens"]
        messages.append({"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"]})

        if not reply["tool_calls"]:
            if terminal_tools is None:
                return finish("REPLIED", reply["content"])
            if nudged:
                return finish("ERROR", reply["content"], error="model stopped without calling a terminal tool")
            nudge = "You must finish by calling one of: " + ", ".join(sorted(terminal_tools)) + "."
            messages.append({"role": "user", "content": nudge})
            nudged = True
            continue

        for call in reply["tool_calls"]:
            name, arguments = call["name"], call["arguments"]
            call_started = time.monotonic()
            output: Any = {"error": f"unknown tool {name!r}"} if name not in allowed_names else await call_tool(name, arguments)
            ok = not _is_error(output)
            calls.append(
                {
                    "name": name,
                    "arguments": arguments,
                    "ok": ok,
                    "error": output.get("error") if isinstance(output, dict) else None,
                    "duration_ms": int((time.monotonic() - call_started) * 1000),
                }
            )
            messages.append({"role": "tool", "tool_name": name, "content": json.dumps(output)})
            if ok and terminal_tools and name in terminal_tools:
                return finish(terminal_tools[name], reply["content"])

    return finish("MAX_STEPS")
