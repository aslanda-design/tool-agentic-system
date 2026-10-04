# Semantic caching, and where it's actually safe to use in this app

This document explains semantic caching — the production technique behind
tools like GPTCache, used to cut LLM latency and cost by reusing answers
to *similar*, not just identical, queries — and designs it for the two
places in this app where it's genuinely safe, after ruling out the
obvious-looking place where it would silently return wrong numbers. Same
pairing style as `docs/AGENTS_AND_MCP_EXPLAINED.md`. Independent of the
other two concept documents, and the cheapest of the three to build.

**Headline finding, and the reason this document spends as much space on
what *not* to cache as on what to build:** `portfolio_assistant` answers
questions about a portfolio whose value, positions, and prices change
continuously (daily snapshots, live syncs, imports). A semantic cache that
returns yesterday's cached answer to "what's my portfolio worth" because
today's phrasing was 96% similar to yesterday's would be a **correctness
bug wearing a performance-optimization costume** — worse than no cache at
all, because it fails silently and looks like a real answer. The right
scope for semantic caching here is narrower and more deliberate than
"cache the chat replies," and getting that scope right is the actual
lesson.

---

## 1. The concept: why *semantic* caching, not just caching

An ordinary cache (an LRU dict, Redis, `functools.lru_cache`) matches on
**exact** keys. That works great for `get_asset(asset_id=17)` — the same
call always means the same thing. It does *not* work for natural-language
queries, because "what's my currency exposure" and "how exposed am I to
different currencies" are the same question in different words, and an
exact-match cache treats them as unrelated. Semantic caching closes that
gap: embed the incoming query, and if a **sufficiently similar** past
query (cosine similarity above a high threshold) already has a stored
answer, return that instead of paying for a new model call. This is a
real, widely deployed production pattern precisely because natural
language has this variability and a plain cache is blind to it.

The part production systems get right, and that a naive implementation
gets wrong, is **knowing which answers are safe to reuse at all** —
semantic *similarity* of the question says nothing about whether the
*answer* is still valid, especially when the answer depends on data that
changes over time. That distinction is §2.

---

## 2. The classification that decides where caching is safe

Split every question this app answers into two buckets:

| Bucket | Example | Cache safety |
|---|---|---|
| **Live-data-dependent** | "What's my portfolio worth", "What's AAPL's price", "What's my drawdown this year" | **Unsafe to cache the answer** beyond the lifetime of the data it reflects — a snapshot refresh, a sync, or an import can invalidate it at any moment |
| **Time-invariant / static knowledge** | "What does the Heston model assume about volatility", "What's the difference between an ETF and a mutual fund", "Explain what a drawdown means" | **Safe to cache** — the correct answer doesn't depend on today's data at all |

Everything this app does that's genuinely safe to semantically cache falls
into one of two categories that respect this split without needing a
per-question classifier model (an extra cost and an extra failure mode —
avoided deliberately, see §4):

1. **Cache the expensive *intermediate* step (embeddings/tool retrieval),
   never the final answer.** Retrieval decisions ("which tools are
   relevant to this phrasing") don't go stale the way portfolio *values*
   do — §3.
2. **Cache whole answers only for tool calls whose result structurally
   cannot change within a cache-worthy window** — e.g. `list_models`
   (the quant model registry, `app/domain/quant/registry.py`, changes only
   when code changes) — §5. This is deliberately *not* semantic (exact
   tool+args matching is enough and cheaper), included here because
   "is this even a semantic-caching problem" is itself part of the lesson.

**Never build:** a semantic cache over full `portfolio_assistant` chat
replies keyed only by question similarity. If you want caching for live
data, cache it the ordinary way (a short, explicit TTL on the underlying
value, e.g. today's snapshot) — that problem doesn't need embeddings, it
needs a TTL, and reaching for the fancier tool here would be the same
mistake `docs/GRAPH_BASED_REASONING.md` §1a warns against for GraphRAG:
solving a problem with a heavier technique than it needs, in a way that
also happens to be less correct.

---

## 3. Where this genuinely helps: Tool RAG's per-turn retrieval

Every `portfolio_assistant` turn with `TOOL_RAG_ENABLED=true` pays one
embedding call plus one hybrid search (`ToolRetriever.select`) to decide
which tools to expose. **Which tools are relevant to "what's my
allocation by sector" is a fact about the *question*, not about today's
portfolio data** — it doesn't go stale when a snapshot refreshes. This
makes retrieval decisions the one clean case in this app where reusing a
result for a near-duplicate question is simply correct, with no asterisk.

```python
# backend/ai/common/tool_rag/semantic_cache.py
"""In-process semantic cache in front of ToolRetriever.select — see
docs/SEMANTIC_CACHING.md §3. Deliberately in-process, not Postgres: this
app has one user and low request volume, so a persistent, shared cache
(the choice tool_index itself made) would be pure overhead here — the
right storage tier depends on scale, and this one doesn't have any.
Caches the RETRIEVAL DECISION (which tools got selected), never a chat
reply — the thing being cached here never goes stale from live data
changing, only from the tool catalog itself changing (§3's dedup note)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class _Entry:
    embedding: np.ndarray
    value: dict  # the {module: tool_names} selection
    created_at: float


class SemanticCache:
    def __init__(self, similarity_threshold: float = 0.97, max_entries: int = 200, ttl_seconds: int = 3600) -> None:
        self._threshold = similarity_threshold
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: list[_Entry] = []

    def get(self, query_embedding: list[float]) -> dict | None:
        vec = np.array(query_embedding)
        now = time.monotonic()
        self._entries = [e for e in self._entries if now - e.created_at < self._ttl_seconds]
        best = max(self._entries, key=lambda e: _cosine(e.embedding, vec), default=None)
        if best is not None and _cosine(best.embedding, vec) >= self._threshold:
            return best.value
        return None

    def put(self, query_embedding: list[float], value: dict) -> None:
        if len(self._entries) >= self._max_entries:
            self._entries.pop(0)  # simplest possible eviction — fine at this app's request volume; see §7 if that stops being true
        self._entries.append(_Entry(np.array(query_embedding), value, time.monotonic()))


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
```

Wired into the retriever at exactly the point the embedding would
otherwise be spent:

```python
# ai/common/tool_rag/retriever.py — ToolRetriever.select, sketch of the addition
_cache = SemanticCache()

async def select(self, query: str, ceiling: dict[str, set[str] | None]) -> tuple[dict, dict | None]:
    all_tools = await resolve_ceiling_tools(ceiling)
    embedding_port = build_embedding_port()
    query_embedding = embedding_port.embed([query])[0]

    cached = _cache.get(query_embedding)
    if cached is not None:
        selection = {module: set(names) for module, names in cached.items()}
        return selection, {"cache_hit": True}

    db = SessionLocal()
    try:
        decision = build_retrieve_tools_use_case(db).retrieve(query, self._pinned, all_tools)
    finally:
        db.close()
    selection = {module: set(names) for module, names in decision.selected.items()}
    _cache.put(query_embedding, selection)
    return selection, dataclasses.asdict(decision)
```

**Note what this does and doesn't save:** it still pays for one embedding
call (needed to check the cache at all) — the saving is the *dense +
lexical search + fusion + policy* pipeline behind it, which is the more
expensive part of `RetrieveToolsUseCase.retrieve`. If your embedding
provider is itself the slow part (a remote API vs. local Ollama), this
cache saves less than it looks like; measure with `evaluate.py`-style
timing before assuming the win, the same discipline every other document
in this series argues for.

**Cache invalidation, the one hard part:** if a new MCP tool is added
(`backend/ai/AGENTS.md` rule 12), the correct tool selection for an
old cached query can genuinely change. Fix: an **epoch counter**, bumped
whenever `build_index` re-runs (§ below), stored alongside each cache
entry; on mismatch, treat the entry as stale regardless of similarity —
the well-known "cache invalidation is one of the two hard problems in
computer science" issue, solved here the standard way (a generation
counter) rather than by guessing a TTL short enough to never matter:

```python
_cache_epoch = 0  # bumped by build_index.py on every real reindex

@dataclass(slots=True)
class _Entry:
    embedding: np.ndarray
    value: dict
    created_at: float
    epoch: int
```

---

## 4. What was deliberately not built: a query-classifier cache for chat answers

The tempting-looking design — classify each incoming question as
"static knowledge" vs. "live-data" (via a cheap model call or a keyword
heuristic) and only semantically cache the static-knowledge bucket's full
answers — was considered and rejected for this app, for a concrete
reason: **the classifier itself can be wrong, and a wrong classification
of a live-data question as static is exactly the silent-stale-data bug
this document opened by warning against.** A misclassified "is my AAPL
position still profitable" (looks conceptual, isn't) returning a cached
answer from three weeks ago is a worse failure than never caching chat
answers at all. Unless a future version of this app adds a large volume
of genuinely static financial-education questions (a "explain this
concept" mode, clearly separated from portfolio questions in the product,
not inferred by a fallible classifier), this bucket isn't worth the risk
it introduces. Revisit only if that product shape actually appears.

---

## 5. The other safe case: plain exact-match caching for structurally static tool results

Not every caching opportunity here needs embeddings — recognizing that is
part of using the right tool for the job. `list_models`
(`ai/mcp_servers/quant/tools/list_models.py`, backed by
`app/domain/quant/registry.py::MODEL_REGISTRY`) returns the exact same
data until the *code* changes (a new model registered) — no live data
involved at all. This needs nothing more than an ordinary in-process TTL
cache keyed by tool name + arguments:

```python
# backend/ai/common/tool_rag/... or a small ai/common/result_cache.py
"""Plain (non-semantic) TTL cache for tool results that are structurally
static — see docs/SEMANTIC_CACHING.md §5. Exact-match keying is correct
and cheaper here; semantic similarity has nothing to add when the
arguments themselves are already the natural cache key."""

import time

_STATIC_TOOL_TTL_SECONDS = {"list_models": 300}  # explicit allowlist — opt-in per tool, never a blanket default
_cache: dict[tuple, tuple[float, object]] = {}


def cached_call(tool_name: str, arguments: dict, compute: callable) -> object:
    ttl = _STATIC_TOOL_TTL_SECONDS.get(tool_name)
    if ttl is None:
        return compute()
    key = (tool_name, tuple(sorted(arguments.items())))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = compute()
    _cache[key] = (time.monotonic(), value)
    return value
```

The **explicit allowlist** (`_STATIC_TOOL_TTL_SECONDS`) is the important
design choice: a new tool is never cached by default, however static it
looks — someone has to deliberately decide "this result cannot become
stale within N seconds" and write that down, the same "explicit
registration, no auto-discovery magic" principle
`app/domain/quant/registry.py`'s own docstring already commits to for a
different reason.

---

## 6. The loop, end to end

```
 user asks a question
        │
        ▼
 embed the question (needed either way, for Tool RAG retrieval)
        │
        ├─▶ SemanticCache.get() — near-duplicate retrieval decision seen before + same epoch?
        │        │
        │       yes → reuse the selected tools, skip the search/fusion/policy pipeline   (§3)
        │       no  → run real retrieval, cache the decision
        ▼
 tools exposed to the model this turn
        │
        ▼
 model runs, calls tools as needed
        │
        ├─▶ a tool in the static-result allowlist (§5)? → exact-match TTL cache, skip recompute
        │
        ▼
 model's final reply — NEVER served from any cache (§2/§4) — always freshly generated from
 whatever live data the tool calls just returned
```

The load-bearing property of this diagram: **every cache in this design
sits strictly upstream of the model's actual reasoning over live data.**
Nothing here ever substitutes a stored *answer* for a live one — only a
stored *retrieval decision* or a stored *structurally-static tool result*,
neither of which can go stale the way a portfolio value can.

---

## 7. Config additions

```python
# app/config.py
tool_rag_semantic_cache_enabled: bool = False   # off by default, same rollout discipline as every other flag here
tool_rag_semantic_cache_threshold: float = 0.97  # deliberately high — this is near-duplicate detection, not topical relevance
tool_rag_semantic_cache_max_entries: int = 200
tool_rag_semantic_cache_ttl_seconds: int = 3600
```

No new infrastructure (no Redis, no new Postgres table) — this is the
concrete payoff of scoping the cache to an in-process, single-user
workload instead of defaulting to "production systems use Redis, so I
should too." If this app's request volume or deployment shape ever
genuinely changes (multi-user, many concurrent requests), that's the
moment to revisit the storage tier — not before, per the same "don't
build the heavier version before the scale that justifies it" reasoning
`docs/GRAPH_BASED_REASONING.md` §3.1 makes about Neo4j.

---

## 8. Tests

- **Unit:** `SemanticCache.get`/`put` — a near-duplicate embedding above
  threshold hits; a topically-related-but-different embedding below
  threshold misses (use two real embeddings of clearly different
  questions from a real embedding call once, saved as a fixture, rather
  than hand-crafted vectors, so the threshold is tuned against reality).
- **Unit:** epoch mismatch forces a miss even when similarity is high —
  this is the test that actually protects against the invalidation bug,
  not just the happy path.
- **Unit:** `cached_call` respects its TTL and its allowlist — a tool not
  in `_STATIC_TOOL_TTL_SECONDS` always calls `compute()`, every time.
- **Regression, explicitly:** a test asserting `portfolio_assistant`'s
  final chat reply is **never** read from any cache introduced by this
  document — e.g. mock the underlying data source to return two different
  values across two calls with a near-identical question, and assert the
  second reply reflects the second value. This is the test that would
  catch the exact failure mode §1's headline finding warns about, so it
  should exist even though (especially because) nothing in this design is
  supposed to need it.

---

## 9. Glossary (continuing `docs/AGENTS_AND_MCP_EXPLAINED.md`'s)

| Term | What it means | Where it lives here |
|---|---|---|
| Semantic cache | Reuses a result for a *similar*, not just identical, past query | `SemanticCache` (§3) |
| Live-data-dependent answer | An answer whose correctness depends on data that changes over time | Never cached whole (§2, §4) |
| Time-invariant answer | An answer that's correct regardless of when it's asked | The only bucket safe to cache whole — not built here for lack of a safe classifier (§4) |
| Cache epoch | A generation counter invalidating every cache entry at once when the underlying corpus changes | §3's `_cache_epoch` |
| Exact-match TTL cache | An ordinary cache keyed on identical inputs, for results that are structurally static | `cached_call` (§5) |

---

## 10. Phased implementation plan

1. **Phase 1 — the exact-match static-tool cache (§5).** Smallest,
   safest, no embeddings involved at all — a good first PR that proves
   the "explicit allowlist, never a blanket default" discipline before
   anything semantic is added.
2. **Phase 2 — the semantic cache over Tool RAG retrieval (§3), including
   the epoch-based invalidation.** Only worth doing once `TOOL_RAG_ENABLED`
   is actually on for a real agent (this whole feature is pointless if
   the pipeline it's caching in front of isn't running) — measure the
   real latency saved with the same before/after timing discipline
   `evaluate.py` already models, per §3's note about embedding-call cost
   dominating in some provider setups.
3. **Not phased — §4's chat-answer cache stays unbuilt** until this app's
   product shape genuinely separates static-knowledge questions from
   live-portfolio questions in a way a classifier isn't needed to
   distinguish.
