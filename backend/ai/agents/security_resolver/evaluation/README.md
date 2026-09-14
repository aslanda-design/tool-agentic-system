# security_resolver evaluation harness

Deterministic, offline evaluation of the `security_resolver` agent
(`ai/agents/security_resolver/agent.py`) against a fixed set of cases — see
`plans/agentic_asset_mapping.md` §6.6. Every case bakes in exactly what
`validate_listing`/`search_listings`/`lookup_isin` would return, so every
model is compared against the exact same world and neither Yahoo nor
OpenFIGI is ever called. `save_security_mapping`/`flag_for_review` are
replayed with the same guards `ResolveSecurityUseCase.accept()` enforces —
a candidate with a stale price or no currency can't be "saved" here either.

The real model backend **is** called — this measures the model, not the
plumbing.

## Run it

```
cd backend
python -m ai.agents.security_resolver.evaluation.evaluate --model qwen3:8b
python -m ai.agents.security_resolver.evaluation.evaluate --model qwen3:8b --model qwen3:14b --repeat 3
```

Every model in one run shares a provider — `settings.agent_provider` by
default, or override with `--provider` (`ollama` or `openai`, see
`ai/common/llm.py`) to sweep a Groq/OpenAI-compatible model instead:

```
python -m ai.agents.security_resolver.evaluation.evaluate --provider openai --model llama-3.3-70b-versatile
```

(needs `AGENT_API_KEY` set in `.env` for that provider). To compare across
providers, run the command twice with different `--provider` values and
diff the two results files.

Prints a Markdown summary table and writes full per-case results to
`results/<timestamp>.json` (gitignored — see `backend/.gitignore`).

Pick the production model (`AGENT_MODEL` in `.env`) by: `safety_violations
== 0` first, then `final_accuracy`, then latency.

## Metrics

| Metric | Definition |
|---|---|
| `final_accuracy` | the run's final tool and (for a save) symbol match `expected` |
| `safety_violations` | saved when `expected` was `flag_for_review`, or saved the wrong symbol — must be 0 |
| `terminated_ok` | ended via a terminal tool, not `MAX_STEPS`/`TIMEOUT`/`ERROR` |
| `invalid_calls` | the model called a tool name that doesn't exist |
| `avg_tool_calls` | mean tool calls per case |
| `p50_latency_s` / `p95_latency_s` | wall-clock seconds per case |
| `avg_prompt_tokens` / `avg_completion_tokens` | from Ollama's own token counts |

Note what `safety_violations` does **not** cover: a save the replay guard
itself rejected (stale price, no currency) never becomes a "safety
violation" — it comes back as `{"error": ...}`, the loop doesn't treat it
as terminal, and the run continues (or ends `MAX_STEPS`/`ERROR` if the model
never recovers). That's a *correctness* failure (`final_accuracy`), not a
safety one — the point of `safety_violations` is catching the model getting
something applied to a real `assets` row that shouldn't have been.

## Cases (`cases/`)

Six to start (`currency_decides_lse_vs_xetra`, `same_currency_tie_eunl_vs_iwda`,
`missing_candidate_via_search`, `all_candidates_stale_must_flag`,
`no_isin_search_and_pick`, `primary_vs_secondary_listing`), all built from
public ISINs — safe to keep in git. See any file in `cases/` for the shape:
`id`, `resolution` (a `get_resolution`-shaped dict), `tool_fixtures` (what
each read tool returns, keyed by its main argument), `expected`
(`final_tool` + `symbol`), `tags`.

Add more over time, especially failures found in production (see an
`agent_runs` row with `status != 'SAVED'/'FLAGGED'` as expected, or a
`RESOLVED_BY_USER` candidate that overrode what the agent picked).

## Exporting a case from your own data

```
python -m ai.agents.security_resolver.evaluation.export_case 42 \
    --expected-tool save_security_mapping --expected-symbol VUSA.L
```

Writes to `cases_local/` (gitignored — a real resolution reflects your own
holdings). Fill in `tool_fixtures` by hand for anything the agent might
look up beyond the candidates already on the resolution.
