# Graph-based reasoning, and how (little of) it fits this app

This document explains what "graph-based reasoning" means for an LLM agent
system — there are three distinct things that name gets applied to, and
they have wildly different costs — and gives an honest recommendation for
this app: build the cheap one (§3), skip the expensive ones (§1a, §1d)
unless a concrete future need appears, and understand the middle one
(§1c) well enough to recognize when *not* to reach for it. It's
independent of `PLANNING_AND_DECOMPOSITION.md` and
`FEEDBACK_LOOPS_AND_FINE_TUNING.md`, and of the three it's the most
speculative — the app's own roadmap flagged this concept with "(yet)",
and the honest conclusion here is that "not yet" can mean "not
really needed at this app's scale" for the heavy versions. The light
version (§3) is worth building regardless, mostly as a genuinely useful,
cheap extension of `portfolio_assistant`.

**Headline finding:** this app's data is already a graph — accounts hold
positions, positions reference assets, assets resolve to listings, listings
belong to exchanges, everything prices in some currency, resolutions have
candidates, quant runs target assets. That graph currently lives entirely
as SQL foreign keys, queried with fixed joins per use case
(`app/domain`/`app/application`). The valuable, cheap move is exposing that
*existing* structure to the agent as something it can traverse dynamically,
not building a new graph database or an LLM-driven knowledge-graph
extraction pipeline — the data was never unstructured text, so the
expensive half of "GraphRAG" (extracting a graph *from* text) has nothing
to do here.

---

## 1. Three things called "graph-based reasoning" — and their real costs

### 1a. GraphRAG — building a knowledge graph *from* unstructured data

The technique popularized as "GraphRAG": run an LLM over a corpus of
documents, extract entities and relationships, build a graph, then answer
questions by traversing/summarizing that graph instead of (or alongside)
plain vector search over document chunks. This is genuinely powerful when
your source data is unstructured text with implicit relationships an LLM
has to *infer* — legal documents, research papers, meeting transcripts.

**This app has no such corpus.** Every fact `portfolio_assistant` might
want — which account holds which asset, which listing an asset resolves
to, which currency it prices in — is already an explicit, correct foreign
key in Postgres. Running an LLM extraction pipeline to *re-discover*
relationships that are already 100%-accurate columns in `orm.py` would be
strictly worse (probabilistic, slow, another provider dependency) than
just reading them. **Do not build this.** If a future version of this app
ever ingests something genuinely unstructured (broker statement PDFs with
free-text notes, say), revisit this section — not before.

### 1b. Graph-structured retrieval over existing relational data

Exposing the *already-correct* relational structure as something an agent
can traverse dynamically — "start at this asset, follow the
`priced_in_currency` edge, then follow `held_by` edges back to every
account exposed to it" — where the *hop count and direction* are decided
per-question by the agent, instead of being baked into one fixed SQL join
per use case. This is the one worth building here, and §3 designs it.

### 1c. Graph-of-Thought — a reasoning *structure*, not a retrieval technique

An extension of Tree-of-Thought (briefly introduced in
`PLANNING_AND_DECOMPOSITION.md` §1) that allows branches to *merge*: e.g.
compute "asset A's expected return" and "asset B's expected return" as two
independent branches, then merge them into "portfolio-level expected
return" in a single combining step, rather than a single strictly-linear
chain of thoughts. It's a genuine framework, and it composes with §5's
sketch — but it costs at minimum 2-3x a single ReAct run (each branch is
its own sub-run, plus a merge step), and it only pays for itself when a
task has *real* independent sub-problems worth solving in parallel and
combining. Every task in this app today (§2) is linear: gather facts,
decide, or answer — nothing branches and merges. Marked "not recommended
now" and sketched only far enough to recognize it later (§5).

### 1d. Graph-structured agent orchestration

Instead of one flat agent per route (this app's current shape — three
agents, each reached by exactly one API route), wire several agents as
nodes in an explicit graph/state machine (this is what frameworks like
LangGraph formalize): a "supervisor" node routes to specialist nodes,
possibly in a cycle. `AGENTS_AND_MCP_EXPLAINED.md` §10 already names this
as exercise #4 ("a supervisor/orchestrator agent"), independent of this
document. At three agents with non-overlapping triggers (one per API
route, no agent ever needs to hand off to another), there is no orchestration
problem to solve yet — this becomes worth it only when you have enough
agents, or overlapping-enough responsibilities, that "which agent handles
this request" stops being a fixed routing decision. Not designed further
here; if you build it, it deserves its own document once there's a second
route-agnostic entry point that actually needs to pick between agents.

---

## 2. Why §1b, concretely: the multi-hop questions this app already has

`portfolio_assistant` can already answer multi-hop questions today, by
chaining tool calls turn-by-turn inside ReAct — e.g. "what's my currency
exposure" already crosses `portfolio` (holdings) and `market_data`
(each asset's pricing currency) via `get_currency_exposure`, an
*application-layer* use case that does this join once, server-side, as
fixed logic. That's fine for a question the app's designer anticipated
and built a use case for. It breaks down for a question that wasn't
anticipated — e.g. "which of my accounts would be affected if I sold my
position in exchange X's version of this ETF" is a *relationship*
question (asset → listing → exchange → which accounts hold *that*
listing specifically) that no existing analytics use case computes, and
today the model has no way to explore that shape except by calling
`list_positions`/`get_asset` repeatedly and doing the graph-walking
*itself*, in its own reasoning, blind to the actual schema.

Exposing the relationship structure as an explicit, traversable tool turns
"the model quietly reconstructs a graph walk out of several ad-hoc reads"
into "the model asks for exactly the traversal it needs, and the tool does
the walking correctly." This is the same "give an agent access to a
capability, deliberately and narrowly, rather than hoping it improvises
correctly" principle behind every MCP tool in this app
(`AGENTS_AND_MCP_EXPLAINED.md` §3.2) — applied to relationships instead of
single facts.

---

## 3. Concrete design: a derived, in-memory relationship graph — not a graph database

### 3.1 Why not Neo4j / a real graph database

A dedicated graph database earns its cost when the graph is large, changes
constantly, and needs graph-native query performance (shortest path,
community detection, etc. over millions of edges). None of that describes
a single user's investment portfolio: dozens of holdings, a few accounts,
a few hundred listings at most. The entire graph for this app's real data
fits comfortably in memory and can be *rebuilt from Postgres on every
call* in low tens of milliseconds — building and discarding it per request
is cheaper than the network round trip to a separate graph database would
be. Adding Neo4j (a new service in `docker-compose.yml`, a new adapter, a
sync pipeline keeping it consistent with Postgres) would be pure overhead
at this data volume — exactly the same judgment call
`plans/tool_rag.md` §1.1 already made about pgvector before this app's
tool catalog grew past ~30 tools: **don't add the heavyweight version of a
capability before the data volume that would justify it actually exists.**
If this app's data volume changes dramatically (e.g. it grows to cover
many users), revisit this.

### 3.2 The graph, built as pure functions over data the repos already return

Same explicit-registration ethos as `app/domain/quant/registry.py` (no
auto-discovery, one file per concern):

```python
# backend/app/domain/graph/types.py
"""Node/edge types for the derived portfolio relationship graph — see
docs/GRAPH_BASED_REASONING.md. This graph is never persisted; it's rebuilt
from the repos on every traversal call (§3.1's cost argument)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NodeKind(Enum):
    ACCOUNT = "account"
    ASSET = "asset"
    LISTING = "listing"
    EXCHANGE = "exchange"
    CURRENCY = "currency"


class EdgeKind(Enum):
    HOLDS = "holds"                    # account -> asset
    RESOLVES_TO = "resolves_to"        # asset -> listing
    LISTED_ON = "listed_on"            # listing -> exchange
    PRICED_IN = "priced_in"            # asset -> currency


@dataclass(frozen=True, slots=True)
class Node:
    kind: NodeKind
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class Edge:
    kind: EdgeKind
    source: Node
    target: Node
```

```python
# backend/app/domain/graph/build.py
"""Pure functions turning already-fetched rows into Node/Edge pairs — no
DB access here (that stays in the application-layer use case, §3.3),
mirroring the existing domain/analytics.py split between pure computation
and data access."""

from __future__ import annotations

from app.domain.graph.types import Edge, EdgeKind, Node, NodeKind


def account_holds_asset_edges(accounts: list, positions: list) -> list[Edge]:
    accounts_by_id = {a.id: Node(NodeKind.ACCOUNT, str(a.id), a.name) for a in accounts}
    return [
        Edge(EdgeKind.HOLDS, accounts_by_id[p.account_id], Node(NodeKind.ASSET, str(p.asset_id), p.symbol))
        for p in positions
        if p.account_id in accounts_by_id
    ]


def asset_resolution_edges(assets: list) -> list[Edge]:
    return [
        Edge(
            EdgeKind.RESOLVES_TO,
            Node(NodeKind.ASSET, str(a.id), a.symbol),
            Node(NodeKind.LISTING, a.listing_ticker, a.listing_ticker),
        )
        for a in assets
        if a.listing_ticker
    ]
```

### 3.3 The traversal use case — BFS, capped, read-only

```python
# backend/app/application/traverse_graph.py
"""Read-only BFS over the derived relationship graph (§3.2). Capped
max_hops keeps both the traversal itself and the token cost of returning
it to the model bounded — see docs/GRAPH_BASED_REASONING.md §3.4."""

from __future__ import annotations

from collections import deque

from app.domain.graph.build import account_holds_asset_edges, asset_resolution_edges
from app.domain.graph.types import Edge, EdgeKind, Node
from app.ports.portfolio import PortfolioPort
from app.ports.assets import AssetPort

MAX_HOPS = 3


class TraverseGraphUseCase:
    def __init__(self, portfolio: PortfolioPort, assets: AssetPort) -> None:
        self._portfolio = portfolio
        self._assets = assets

    def neighbors(self, start: Node, max_hops: int = MAX_HOPS) -> list[Edge]:
        max_hops = min(max_hops, MAX_HOPS)
        edges = account_holds_asset_edges(self._portfolio.list_accounts(), self._portfolio.list_positions())
        edges += asset_resolution_edges(self._assets.list_assets())

        adjacency: dict[Node, list[Edge]] = {}
        for edge in edges:
            adjacency.setdefault(edge.source, []).append(edge)
            adjacency.setdefault(edge.target, []).append(edge)

        visited = {start}
        frontier = deque([(start, 0)])
        reached: list[Edge] = []
        while frontier:
            node, depth = frontier.popleft()
            if depth >= max_hops:
                continue
            for edge in adjacency.get(node, []):
                other = edge.target if edge.source == node else edge.source
                if other in visited:
                    continue
                visited.add(other)
                reached.append(edge)
                frontier.append((other, depth + 1))
        return reached
```

### 3.4 The MCP tool — thin, exactly like every other tool in this app

```python
# backend/ai/mcp_servers/portfolio/tools/explore_relationships.py
"""Traverses the portfolio's relationship graph from one starting entity —
see docs/GRAPH_BASED_REASONING.md. Read-only by construction: no write
path exists on this graph at all, so there's nothing to guard (contrast
with backend/ai/AGENTS.md rule 6, which is about tools that DO write)."""

from app.adapters.persistence.session import SessionLocal
from app.container import build_traverse_graph_use_case
from app.domain.graph.types import Node, NodeKind
from ai.common.jsonable import to_jsonable


def explore_relationships(entity_kind: str, entity_id: str, max_hops: int = 2) -> list[dict]:
    """Explore what's connected to one account/asset/listing/exchange, up
    to a few hops away — e.g. "which accounts hold this asset" or "what
    exchange is this asset's listing on."

    Args:
        entity_kind: One of "account", "asset", "listing", "exchange".
        entity_id: The entity's id (or symbol, for an asset/listing).
        max_hops: How many relationship hops to follow (capped at 3).

    Returns:
        A list of {kind, source, target} edges reachable within max_hops.
    """
    db = SessionLocal()
    try:
        use_case = build_traverse_graph_use_case(db)
        start = Node(NodeKind(entity_kind), entity_id, entity_id)
        return [to_jsonable(edge) for edge in use_case.neighbors(start, max_hops)]
    finally:
        db.close()
```

Wiring it into `portfolio_assistant` is the same one-line addition every
other capability in this app is:

```python
# ai/agents/portfolio_assistant/agent.py — SERVERS dict, one new entry
"ai.mcp_servers.portfolio": {"get_portfolio_summary", "list_positions", "get_allocation", "get_value_history", "explore_relationships"},
```

Nothing about `run_agent`, `open_mcp_servers`, or the loop changes — this
is the payoff of the app's existing architecture, not a new pattern.

### 3.5 Cost, made concrete

Building the whole graph is O(number of holdings + number of assets) —
for a single-user portfolio, low tens to low hundreds of nodes/edges,
comfortably sub-50ms to construct fresh in Python on every call, well
under the latency of the model round trip that requested it. **Don't add
caching until you've actually measured this being slow** — the same
"measure before you optimize" discipline the other two documents argue
for planning calls and reflection steps. If this app's data volume ever
makes rebuilding per-call measurably slow, the fix is a short-TTL
in-process cache keyed by nothing (there's only one portfolio), not a
persistent graph store.

### 3.6 Guardrails

- **Read-only by construction** — there is no write method on
  `TraverseGraphUseCase` at all, so this doesn't need the write-guard
  discipline `backend/ai/AGENTS.md` rule 6 requires for mutating tools;
  there's nothing to misuse.
- **`max_hops` is capped server-side (3), not just defaulted** — the tool
  clamps whatever the model asks for, so a confused model asking for
  `max_hops=50` can't blow up the response size or the tokens fed back to
  it next turn.
- **This tool needs `example_queries` like every other new tool**
  (`backend/ai/AGENTS.md` rule 12), even if `TOOL_RAG_ENABLED` stays
  `false` for now:

```json
// backend/ai/common/tool_rag/examples/portfolio.json — new entry
{
  "explore_relationships": {
    "example_queries": [
      "which accounts hold this ETF",
      "what exchange is my Vanguard fund listed on",
      "show me everything connected to this asset"
    ]
  }
}
```

---

## 4. Where this doesn't help — restated plainly

If a question is answerable by one existing analytics use case
(`get_returns`, `get_concentration`, `get_drawdown`, `get_currency_exposure`),
**that's still the better tool for it** — those already compute the
correct aggregate in one server-side pass; `explore_relationships` returns
raw edges for the model to reason over, which costs more tokens and more
model reasoning for something a fixed use case already does exactly right.
The graph tool is for questions about *structure and connectivity* the
analytics layer wasn't written to answer, not a replacement for it.

---

## 5. Graph-of-Thought, sketched only far enough to recognize later

If a genuinely branch-and-merge task ever appears (§1c) — for illustration
only, not a recommendation to build now:

```python
# NOT part of this plan's phases — illustrative only, see §1c
async def graph_of_thought_example(sub_questions: list[str], merge_prompt: str, chat_client, tools, call_tool):
    branch_results = await asyncio.gather(*[
        run_agent(system_prompt=..., user_message=q, tools=tools, call_tool=call_tool,
                  chat_client=chat_client, max_steps=4, terminal_tools=None)
        for q in sub_questions
    ])
    merged_input = "\n\n".join(r.final_message for r in branch_results)
    return chat_client.chat([
        {"role": "system", "content": merge_prompt},
        {"role": "user", "content": merged_input},
    ], tools=[])
```

The only structural difference from `PLANNING_AND_DECOMPOSITION.md` §5's
`run_plan` is `asyncio.gather` (branches run in parallel, not sequentially)
and an explicit merge call at the end instead of just returning the last
step's result. It costs roughly `len(sub_questions)` parallel model calls
plus one merge call — parallel, so wall-clock latency is closer to the
slowest branch than the sum of all of them, but token/dollar cost is still
the sum. Build this only against a real task that has independent
sub-problems worth solving separately and combining — nothing in this app
does yet.

---

## 6. Config additions

None required. `MAX_HOPS` is a code constant (§3.3), not a runtime
setting — there's no operational reason to tune it per environment, and an
untunable cap is one fewer thing that can be misconfigured.

---

## 7. Tests

- **Unit, pure functions:** `account_holds_asset_edges`/
  `asset_resolution_edges` against small fixture lists — no DB, no MCP,
  same style as `app/domain/analytics.py`'s existing pure-function tests.
- **Unit:** `TraverseGraphUseCase.neighbors` against a fake
  `PortfolioPort`/`AssetPort` (extend `backend/tests/fakes.py`) — assert
  `max_hops` is actually respected (a graph with a 5-hop chain, requesting
  `max_hops=2`, must not return hop-3+ edges), and that the cap silently
  clamps a too-large request rather than erroring.
- **Integration, real stdio round trip:** `explore_relationships` via a
  real `McpToolSession`, following the pattern in
  `backend/tests/test_mcp_client_multi.py` — this is the layer that would
  have caught the "bare dict return" SDK gotcha documented in
  `backend/ai/AGENTS.md`, so any new tool should get at least one real
  (not mocked) round trip.

---

## 8. Glossary (continuing `docs/AGENTS_AND_MCP_EXPLAINED.md`'s)

| Term | What it means | Where it lives here |
|---|---|---|
| GraphRAG | LLM-extracted knowledge graph from unstructured text, then queried | Not applicable — this app's data is already structured (§1a) |
| Relationship graph | The existing relational schema, exposed as explicit traversable nodes/edges | `app/domain/graph/` (§3.2) |
| Traversal | Walking the graph from a start node up to N hops | `TraverseGraphUseCase.neighbors` (§3.3) |
| Graph-of-Thought | Branch into independent sub-problems, merge results back together | Sketched, not built (§5) |
| Agent orchestration graph | Multiple agents wired as nodes with routing/handoff between them | Not designed here — see `AGENTS_AND_MCP_EXPLAINED.md` §10 exercise #4 |

---

## 9. Phased implementation plan

1. **Phase 1 — the graph itself, no agent wiring.** `app/domain/graph/`
   (types + build functions) and `TraverseGraphUseCase`, fully unit
   tested (§7's first two bullets), with no MCP tool yet — this phase is
   "the derived graph is correct," provable without touching `ai/` at all.
2. **Phase 2 — the MCP tool + wiring into `portfolio_assistant`.**
   `explore_relationships`, its `example_queries` entry, the one-line
   `SERVERS` addition, and the real stdio integration test (§7's third
   bullet).
3. **Phase 3 (only if a real use case shows up needing it) — extend
   `NodeKind`/`EdgeKind`** to cover more of the schema (currencies,
   quant runs, resolution candidates) the same way `domain/quant/registry.py`
   grows by adding one file per model — don't pre-build every possible
   node/edge kind speculatively; add one when a real question needs the
   edge that's missing.
4. **Not phased — §1a, §1d, and the built version of §1c/§5 stay
   unbuilt** until a concrete need (unstructured data ingestion, multiple
   overlapping agents, or a genuinely branch-and-merge task,
   respectively) actually appears in this app.
