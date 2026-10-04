# Semantic long-term memory, and how to add it to `portfolio_assistant`

This document explains semantic long-term memory — the production pattern
behind "ChatGPT remembers things about you" and libraries like `mem0` —
and designs it for `portfolio_assistant`, the one agent in this app shaped
like a long-lived conversational assistant rather than a one-shot task
runner. Same pairing style as `docs/AGENTS_AND_MCP_EXPLAINED.md`. It
reuses the exact embedding infrastructure Tool RAG already built
(`EmbeddingPort`, pgvector), so building this is smaller than it looks —
most of the hard architectural decisions were already made by
`plans/tool_rag.md` and just need to be applied to a second kind of
document. Independent of the other concept documents.

**Headline finding:** `portfolio_assistant` already has *session* memory
(`_load_history` replays up to `MAX_HISTORY_MESSAGES=20` rows from the
*current* `chat_sessions` thread) but zero memory *across* sessions. Start
a new chat tomorrow and it has no idea you told it yesterday that you're
saving for a house in three years, or that you specifically don't want
crypto mentioned in recommendations. That's the actual gap — this document
is about closing it, not about replacing what already works well within
one session.

---

## 1. The concept: episodic memory vs. semantic memory, and why the second one needs retrieval

Borrowing the standard cognitive-science split production memory systems
use:

- **Episodic memory** — "what happened, in order, in this conversation."
  This is exactly what `_load_history` already does: replay the raw turns
  of one session. It's complete but it doesn't scale — you can't replay
  every session you've ever had into every new prompt (token cost grows
  without bound, and most of it would be irrelevant to today's question).
- **Semantic memory** — "durable facts extracted from past experience,"
  decoupled from *which* conversation they came from. "The user's
  investment horizon is 2045." "The user is uncomfortable with leveraged
  ETFs." These facts are small, few, and most of them are irrelevant to
  any *given* question — which is exactly the shape a retrieval problem
  has. This is why production memory systems (`mem0`, ChatGPT's memory
  feature) implement semantic memory as **RAG over a small, auto-curated
  corpus of facts about the user**, rather than as more context stuffed
  into every prompt: embed each fact once, embed the new question, retrieve
  only the facts relevant to *this* question, inject just those.

That RAG shape — embed, store, retrieve top-k by similarity, inject — is
precisely what `ai/common/tool_rag/` already does for *tool descriptions*.
Semantic memory is the same pipeline pointed at a different, much smaller
corpus (facts about one user, not ~30 tool schemas). This is worth
internalizing as the general lesson: **"semantic memory" and "RAG" are not
two different techniques — long-term agent memory is a narrow, specific
application of retrieval-augmented generation**, and once you've built one
RAG pipeline in an app, adding a second corpus is mostly plumbing, not new
architecture.

---

## 2. What already exists, and exactly what's missing

```
                    portfolio_assistant TODAY
 chat_sessions / chat_messages (Postgres, migration 0004)
        │
        ▼
 _load_history(session_id)  →  last 20 messages, THIS session only
        │
        ▼
 run_agent(..., history=...)


                    ai/common/tool_rag/ TODAY (a different corpus, same shape)
 tool_index (pgvector + pg_trgm, migration 0006)
        │
        ▼
 ToolRetriever.select(query, ceiling)  →  embed query, hybrid search, fuse, pick top-k
        │
        ▼
 run_agent(..., tools=...)
```

The missing piece is a **third** table with the same pgvector shape as
`tool_index`, holding facts instead of tool descriptions, plus two new
pieces of logic: something that *writes* facts (extraction, after a turn)
and something that *reads* them (retrieval, before a turn) — both modeled
directly on code that already exists and is already tested.

---

## 3. Storage — `agent_memories`, the same shape as `tool_index`

```python
# backend/migrations/versions/0007_agent_memories.py — pick the next real
# revision number when implementing; check migrations/versions/ first, in
# case another plan already took 0007.
"""agent_memories — semantic long-term memory for portfolio_assistant.
See docs/SEMANTIC_LONG_TERM_MEMORY.md. Same pgvector shape as tool_index
(migration 0006) — a small, auto-curated corpus of durable facts, embedded
the same way tool descriptions are."""

def upgrade() -> None:
    op.create_table(
        'agent_memories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding_model', sa.String(length=60), nullable=False),
        sa.Column('embedding', Vector(768), nullable=False),
        sa.Column('source_session_id', sa.Integer(), sa.ForeignKey('chat_sessions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute("CREATE INDEX agent_memories_embedding_idx ON agent_memories USING hnsw (embedding vector_cosine_ops)")
```

```python
# backend/app/ports/memory.py — mirrors app/ports/tool_rag.py::ToolIndexRepo exactly
class MemoryRepo(ABC):
    @abstractmethod
    def add(self, text: str, embedding: list[float], embedding_model: str, source_session_id: int | None) -> None: ...

    @abstractmethod
    def search(self, query_embedding: list[float], embedding_model: str, limit: int) -> list[MemoryHit]:
        """Cosine-similarity search — same query shape as
        ToolIndexRepo.dense_search, minus the candidate_keys restriction
        (there's no authorization ceiling here; every stored memory
        belongs to the one user this app serves)."""

    @abstractmethod
    def find_similar(self, embedding: list[float], embedding_model: str, threshold: float) -> MemoryHit | None:
        """Nearest existing memory above `threshold` — used by §5's dedup
        step before inserting a new one, so "user likes index funds"
        doesn't get stored fifty slightly-differently-worded times."""

    @abstractmethod
    def touch(self, memory_id: int) -> None:
        """Bumps last_used_at — feeds §6's pruning policy."""
```

No new `EmbeddingPort` implementation is needed — `build_embedding_port()`
(`app/adapters/embeddings/factory.py`) already returns whatever backend
`TOOL_RAG_EMBEDDING_PROVIDER` points at, and this feature reuses it
directly. This is the concrete payoff of Tool RAG having been built as a
clean port/adapter rather than inline code: a second, unrelated feature
gets embeddings for free.

---

## 4. Writing memories — extraction, off the request path

The critical latency decision: **extraction never happens on the turn the
user is waiting on.** After `_persist_turn` already saves the exchange and
the user already has their reply, a background task asks the model one
cheap, tool-less question: is there a durable fact worth remembering here?

```python
# backend/app/application/extract_memory.py
"""Runs after a chat turn is already answered — never on the request path
the user is waiting on (see docs/SEMANTIC_LONG_TERM_MEMORY.md §4). A
missed or slow extraction costs nothing but that one fact not being
remembered; it must never make a chat reply slower."""

from __future__ import annotations

import json

from ai.common.llm import ChatClient
from app.ports.memory import MemoryRepo
from app.ports.tool_rag import EmbeddingPort

_EXTRACTION_PROMPT = """Given this exchange, is there a durable fact worth \
remembering about the user for future conversations (a preference, a goal, \
a constraint) — as opposed to a one-off question with no lasting \
relevance? Reply with ONLY a JSON object: {"fact": "..."} or {"fact": null}."""


class ExtractMemoryUseCase:
    def __init__(self, memory_repo: MemoryRepo, embedding_port: EmbeddingPort) -> None:
        self._memory_repo = memory_repo
        self._embedding_port = embedding_port

    def extract(self, user_message: str, assistant_reply: str, chat_client: ChatClient, session_id: int) -> None:
        messages = [
            {"role": "system", "content": _EXTRACTION_PROMPT},
            {"role": "user", "content": f"User: {user_message}\nAssistant: {assistant_reply}"},
        ]
        reply = chat_client.chat(messages, tools=[])
        fact = self._parse_fact(reply["content"])
        if fact is None:
            return
        embedding = self._embedding_port.embed([fact])[0]
        existing = self._memory_repo.find_similar(embedding, self._embedding_port.model_name, threshold=0.92)
        if existing is not None:
            self._memory_repo.touch(existing.id)  # already known — reinforce, don't duplicate (§6)
            return
        self._memory_repo.add(fact, embedding, self._embedding_port.model_name, session_id)

    @staticmethod
    def _parse_fact(raw: str) -> str | None:
        try:
            data = json.loads(raw)
            fact = data.get("fact")
            return fact if isinstance(fact, str) and fact.strip() else None
        except (json.JSONDecodeError, AttributeError):
            return None
```

Wired from `PortfolioAssistantAgent._send_message` as a genuine
fire-and-forget background task — the same "this must never block or fail
the user-facing request" discipline `_run_loop`'s own exception handling
already enforces:

```python
# ai/agents/portfolio_assistant/agent.py — after _persist_turn, in _send_message
if settings.memory_enabled and result.status == "REPLIED":
    asyncio.create_task(self._extract_memory_safely(session_id, user_message, result.final_message))
```

```python
async def _extract_memory_safely(self, session_id: int, user_message: str, reply: str) -> None:
    try:
        db = SessionLocal()
        try:
            build_extract_memory_use_case(db).extract(user_message, reply, build_chat_client(), session_id)
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — a missed memory is a non-event; it must never surface as a user-facing error
        logger.warning("memory extraction failed", exc_info=True)
```

`asyncio.create_task` returns immediately — the HTTP response to the user
has already gone out before this coroutine even starts running its first
`await`. This is the whole latency story for writing memories: **zero
added latency on the turn that triggers it**, at the cost of one extra
model call happening somewhere in the background, roughly once per turn
(gate this — see §6 — so it isn't literally every single turn once volume
grows).

---

## 5. Reading memories — retrieval, on the request path (bounded, budgeted)

Unlike extraction, retrieval **does** sit on the critical path — the whole
point is injecting relevant facts *before* the model answers. This is the
one place this feature adds real per-turn latency, and it's worth being
precise about how much: exactly the same shape and cost as Tool RAG's
existing per-turn retrieval (`ToolRetriever.select`, already accepted into
this app's latency budget) — one embedding call plus one vector search,
not a new category of cost:

```python
# backend/app/application/retrieve_memories.py
"""Mirrors app/application/retrieve_tools.py's shape exactly — same
"embed the query, search, return the hits" pipeline, different corpus.
See docs/SEMANTIC_LONG_TERM_MEMORY.md §5."""

class RetrieveMemoriesUseCase:
    def __init__(self, memory_repo: MemoryRepo, embedding_port: EmbeddingPort) -> None:
        self._memory_repo = memory_repo
        self._embedding_port = embedding_port

    def retrieve(self, query: str, limit: int = 5) -> list[str]:
        embedding = self._embedding_port.embed([query])[0]
        hits = self._memory_repo.search(embedding, self._embedding_port.model_name, limit)
        for hit in hits:
            self._memory_repo.touch(hit.id)
        return [hit.text for hit in hits]
```

```python
# ai/agents/portfolio_assistant/agent.py — _run_loop, one new step
async def _run_loop(self, history: list[dict], user_message: str) -> tuple[AgentRunResult, dict | None]:
    ceiling, tool_retrieval = await _select_tools(user_message)
    memories = await asyncio.to_thread(_retrieve_memories, user_message) if settings.memory_enabled else []
    async with open_mcp_servers(ceiling) as session:
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        if memories:
            prompt += "\n\nWhat you already know about this user:\n" + "\n".join(f"- {m}" for m in memories)
        result = await run_agent(
            system_prompt=prompt, user_message=user_message, tools=await session.tool_schemas(),
            call_tool=session.call_tool, chat_client=build_chat_client(),
            max_steps=settings.agent_max_steps, terminal_tools=None, history=history,
        )
        return result, tool_retrieval
```

Appending retrieved facts to the **system prompt** rather than to
`user_message` is a deliberate choice, not a stylistic one: it keeps
`_load_history`'s replayed turns and the current question exactly as they
were (so history stays a faithful record of the actual conversation),
while still making the facts available to the model every step of this
turn's loop, not just the first one.

---

## 6. Memory hygiene — the part that's easy to get wrong

A memory system that only ever grows is a liability, not a feature: it
gets slower to search, more likely to surface stale or contradictory
facts, and — since these are your real financial preferences and goals —
worth treating with the same care as any other real personal data in this
app.

- **Dedup on write** (already in §4's `extract`): a new fact within
  `threshold=0.92` cosine similarity of an existing one reinforces
  (`touch`) instead of duplicating. Tune the threshold empirically —
  Tool RAG's own `tool_rag_min_score=0.35` for a *different* kind of
  match (relevance, not near-duplication) is not the right reference
  point; dedup needs a much higher bar than relevance-retrieval does.
- **Prune on a schedule, not on the request path.** A memory nobody has
  needed in months (`last_used_at` far in the past) is more likely to be
  stale than useful — a periodic offline job (same "batch, out-of-band"
  philosophy as `docs/FEEDBACK_LOOPS_AND_FINE_TUNING.md`'s fine-tuning
  loop) deletes memories unused past some horizon, e.g. 180 days.
- **Give yourself visibility and control.** Since these facts influence
  real advice, expose `GET /api/memories` and `DELETE /api/memories/{id}`
  — a simple settings-page list, not a hidden black box. This mirrors the
  existing `ai_notes` pattern (visible, dismissible) rather than
  inventing a new UX idea.
- **Don't let extraction run every single turn once conversations get
  long.** Gate it (e.g. only on turns past some minimum length, or at most
  once per N turns) so the background model-call volume doesn't grow
  unboundedly with usage — a cheap guard, since this app's whole traffic
  volume is one user's, but worth building the discipline correctly.

---

## 7. Config additions

```python
# app/config.py
memory_enabled: bool = False        # off by default, same rollout shape as tool_rag_enabled
memory_dedup_threshold: float = 0.92
memory_retrieval_top_k: int = 5
memory_prune_after_days: int = 180
```

No new embedding provider settings — reuses
`tool_rag_embedding_provider`/`_model`/`_base_url`/`_api_key` directly
(worth renaming those to a shared `embedding_*` prefix once a second
feature depends on them, so the setting name doesn't imply it's
Tool-RAG-specific — a small cleanup, not a blocker to building this).

---

## 8. Tests

- **Unit:** `ExtractMemoryUseCase._parse_fact` against valid JSON,
  `{"fact": null}`, and malformed JSON — assert `None` in every failure
  case (never crash a background task on bad model output).
- **Unit:** `ExtractMemoryUseCase.extract` against a fake `MemoryRepo` and
  fake `EmbeddingPort` — assert `find_similar` above threshold calls
  `touch`, not `add`; assert below threshold calls `add`.
- **Unit:** `RetrieveMemoriesUseCase.retrieve` against fixtures — assert
  the returned list is just `hit.text`, in the repo's order, and that
  `touch` is called for every hit returned (so `last_used_at` genuinely
  reflects use, feeding §6's pruning correctly).
- **Integration:** one real end-to-end test asserting a fact stated in
  session A is retrieved and influences a reply in session B — this is
  the actual feature; unit tests alone can't prove cross-session memory
  works.

---

## 9. Glossary (continuing `docs/AGENTS_AND_MCP_EXPLAINED.md`'s)

| Term | What it means | Where it lives here |
|---|---|---|
| Episodic memory | Replaying the literal turns of one conversation | `_load_history` (already built) |
| Semantic memory | Durable facts, decoupled from which conversation produced them | `agent_memories` (§3) |
| Extraction | Turning a conversation turn into a candidate durable fact | `ExtractMemoryUseCase` (§4), off the request path |
| Retrieval | Embedding the new query and searching stored memories for relevant ones | `RetrieveMemoriesUseCase` (§5), on the request path |
| Dedup threshold | The similarity bar above which a "new" fact is treated as already known | `memory_dedup_threshold` (§6) |
| Memory pruning | Periodically deleting memories that haven't been useful in a while | §6, offline/scheduled |

---

## 10. Phased implementation plan

1. **Phase 1 — storage + retrieval only, manually seeded.** Build
   `agent_memories`, `MemoryRepo`, `RetrieveMemoriesUseCase`, and the
   `_run_loop` wiring, but skip automatic extraction — seed a couple of
   memories by hand (a tiny script or direct SQL) and confirm retrieval
   and prompt injection actually change a real reply. This isolates "does
   retrieval work" from "does extraction produce good facts," which is
   the harder problem.
2. **Phase 2 — automatic extraction, gated and backgrounded.**
   `ExtractMemoryUseCase`, the `asyncio.create_task` wiring, dedup. Watch
   real extracted facts for a while before trusting them — a small local
   model asked to "extract a durable fact" can produce noise; this phase
   is where you tune the extraction prompt against real output.
3. **Phase 3 — hygiene.** Pruning job, the visibility/delete endpoints.
   Not optional long-term, but fine to defer until Phase 2 has actually
   accumulated enough memories for staleness to matter.
