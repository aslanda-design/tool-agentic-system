# Documentation index

This app was built to learn agentic/LLM-systems engineering as much as to
track a portfolio, so the design process is written down and versioned
alongside the code — not thrown away after implementation. Two kinds of
document live here and in `../plans/`:

## `docs/` — concept studies

Each pairs a general technique from the agentic-systems literature with the
real file in this app that already implements part of it, and ends with an
honest recommendation — including, twice, a recommendation *not* to build
the heavier version. Independent of each other; read in any order.

| Document | What it covers | Verdict |
|---|---|---|
| [`AGENTS_AND_MCP_EXPLAINED.md`](AGENTS_AND_MCP_EXPLAINED.md) | How MCP servers, tool-calling agent loops, and the provider-agnostic LLM client actually work — walked through against this repo's own 7 servers / 3 agents | Already a working example; explains what's here |
| [`PROMPT_INJECTION_DEFENSES.md`](PROMPT_INJECTION_DEFENSES.md) | OWASP LLM Top 10's #1 risk for tool-calling agents | Found a real vulnerable path in this app's own `security_resolver`, and fixes it |
| [`SEMANTIC_CACHING.md`](SEMANTIC_CACHING.md) | Reusing LLM answers across *similar*, not just identical, queries | Two places it's safe here; one place it would silently return wrong numbers |
| [`SEMANTIC_LONG_TERM_MEMORY.md`](SEMANTIC_LONG_TERM_MEMORY.md) | Persistent cross-session memory (the "ChatGPT remembers you" pattern) | Designed for `portfolio_assistant`, reusing Tool RAG's existing embedding infra |
| [`PLANNING_AND_DECOMPOSITION.md`](PLANNING_AND_DECOMPOSITION.md) | Multi-step planning frameworks for LLM agents | Phased build plan; where it would and wouldn't earn its keep |
| [`FEEDBACK_LOOPS_AND_FINE_TUNING.md`](FEEDBACK_LOOPS_AND_FINE_TUNING.md) | Online fine-tuning vs. a genuinely useful lighter version | Draws a hard line between what's worth building and what just sounds impressive |
| [`GRAPH_BASED_REASONING.md`](GRAPH_BASED_REASONING.md) | Three different things "graph-based reasoning" gets called | Build the cheap one; skip the expensive ones without a concrete need |

## `../plans/` — design documents

Status-tracked proposals for features actually built in this repo, several
carrying an "Implementation notes — as built vs. as designed" section
written *after* the code shipped: what changed, what broke an original
assumption, what a synthetic test caught before real data would have.

| Document | Feature | Status |
|---|---|---|
| [`agentic_asset_mapping.md`](../plans/agentic_asset_mapping.md) | Security resolver (ISIN → priceable listing) | Phases 1-6 implemented |
| [`agentic_asset_mapping_phase7_8.md`](../plans/agentic_asset_mapping_phase7_8.md) | ML ranker + portfolio-intelligence agents | Phase 8 implemented, Phase 7 (ML ranker) not started |
| [`quant_lab.md`](../plans/quant_lab.md) | Pluggable quant-model registry + Monte Carlo engine | Phases 1-6 implemented |
| [`quant_lab_phase7_8.md`](../plans/quant_lab_phase7_8.md) | Black-Scholes (GBM) + Heston | Implemented |
| [`quant_lab_phase9_rough_heston.md`](../plans/quant_lab_phase9_rough_heston.md) | Rough Heston (fractional-kernel volatility) | Implemented |
| [`quant_lab_phase10_hawkes.md`](../plans/quant_lab_phase10_hawkes.md) | Hawkes-driven jump-diffusion | Implemented |
| [`tool_rag.md`](../plans/tool_rag.md) | Retrieval-based per-turn tool selection | Phases 1-3 implemented |
| [`market_intel.md`](../plans/market_intel.md) | External news/filings/sentiment MCP tools | Phases 1-3 implemented |

For the current, authoritative status of every phase (what's built, what's
verified against real data vs. fixtures, what's explicitly descoped), see
the root [`AGENTS.md`](../AGENTS.md)'s roadmap section — these plans are the
*design* record, that file is the *status* record, and the two are kept in
sync deliberately.
