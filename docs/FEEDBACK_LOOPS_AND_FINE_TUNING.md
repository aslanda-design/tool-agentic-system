# Feedback loops and fine-tuning, and how to add them to this app

This document explains what "feedback loops" and "online fine-tuning" mean
for an LLM agent system, draws a hard line between the version that's
genuinely useful here and the version that sounds impressive but is a bad
fit, and gives a phased plan for the version worth building. It follows
the same pairing style as `docs/AGENTS_AND_MCP_EXPLAINED.md`: concept, then
the real file in this repo that already implements half of it, then what's
missing. It's independent of `PLANNING_AND_DECOMPOSITION.md` and
`GRAPH_BASED_REASONING.md`, and of the three concept documents it's the
lowest-risk one to build first — everything in §4 is additive around code
that already exists (`evaluate.py`) and touches production latency not at
all, since every step happens out of band from a real chat/resolution
request.

**Headline finding:** this app already has the *measurement* half of a
feedback loop (`security_resolver`'s offline evaluation harness). What it's
missing is the *collection* half — a way for a real correction (you telling
the app "that was wrong") to turn into a case the harness can measure
against, and later into training data. Building that connective tissue is
the actual, valuable version of "feedback loops." Literal **online**
fine-tuning — continuously updating model weights from live traffic — is,
argued in §1.4, close to always the wrong tool for an app shaped like this
one, and the plan in this document deliberately does not build it.

---

## 1. Four things people mean by "feedback loop" — don't conflate them

### 1.1 Inference-time feedback: reflection / self-critique, no weights change

The model critiques or re-checks its own output *within the same run*,
before returning it — e.g. "here's your draft answer; here's a second
prompt asking you to check it against the source data before finishing."
No training happens; this is closer to an extra ReAct turn than to
"learning." Cost: one extra model call, paid on every run that uses it.
This app doesn't do this anywhere today, and §5 explains where (if
anywhere) it would be worth its latency tax.

### 1.2 Evaluation-time feedback: measure, don't yet act

Running an agent against a fixed suite of cases with a real model and
reporting whether it got them right — this **already exists**, in full, in
`ai/agents/security_resolver/evaluation/evaluate.py`. It's "feedback" in
the sense that it produces a signal about quality, but nothing consumes
that signal automatically; a human reads the printed table and decides
whether to change a prompt, swap a model, or ship. §2 walks through
exactly what it measures.

### 1.3 Training-time feedback: fine-tuning (SFT / DPO / RLHF)

Taking accumulated examples — correct outputs, or pairs of (better, worse)
outputs — and using them to update a model's weights, **out of band**,
producing a new model artifact you evaluate before you use it. This is
"feedback" in the fullest sense: real usage changes the model, eventually.
It's also slow, deliberate, and — critically for this app — a batch
process you control, not something that happens mid-request. §4.3 designs
a lightweight version of this that fits a single-user local app.

### 1.4 Online fine-tuning specifically — and why not to build it here

"Online fine-tuning" narrows §1.3 to: update weights continuously, from
live production traffic, with no human review gate between "the model saw
a new example" and "the model that answers the next request is different."
This is a real, legitimate technique — in large-scale, high-traffic systems
with the infrastructure to safely canary and roll back a model update.
None of that applies here:

- **This app has one user.** The entire training signal for a day is
  however many chat turns and resolution reviews you personally generate —
  nowhere near enough examples per unit time for online updates to be
  stable rather than noisy.
- **A local small model (`qwen3:8b` via Ollama) has no safe live-swap
  story.** There's no shadow-traffic canary, no automatic rollback if an
  update makes things worse mid-conversation — you'd be debugging "why did
  the assistant suddenly get worse" with no evaluation gate in between.
- **You explicitly don't want your own assistant's behavior drifting under
  you between sessions with no record of why.** `security_resolver`'s whole
  design principle (§6 of `AGENTS_AND_MCP_EXPLAINED.md`) is that writes go
  through the same guard a human path uses, and every agent run is
  recorded so behavior is explainable after the fact. A model that
  silently re-weights itself from your last conversation is the opposite
  of that — unexplainable behavior change with no audit trail.
- **The batch, evaluated version (§1.3) gets you the same end state —
  the model gets better at *your* portfolio's quirks over time — without
  any of the above risk**, at the cost of the update being periodic
  (weekly/monthly, whenever enough new cases accumulate) instead of
  instant. For a personal tool you use daily, "the assistant gets a bit
  better after each deliberate review pass" is a completely adequate
  cadence, and it's the one this document builds.

---

## 2. What already exists: the "measure" half

`evaluate.py` (read in full when preparing this document) does exactly
this, per model:

```
cases/*.json + cases_local/*.json  →  run_agent (real model, ReplaySession
                                       standing in for the real MCP server —
                                       no real Yahoo/OpenFIGI calls, no real
                                       DB writes)
                                    →  per-case: final_accuracy,
                                       safety_violation, terminated_ok,
                                       invalid_calls, tool_calls, latency_s,
                                       token counts
                                    →  summarized per model, printed as a
                                       Markdown table, saved to
                                       evaluation/results/<timestamp>.json
```

Two details worth internalizing because §4 reuses both:

- **`cases/` (public, committed) vs. `cases_local/` (gitignored, your real
  data)** already exists as a split — `_load_cases()` reads both
  directories and merges them. This is the exact mechanism §4.1 plugs real
  corrections into, with zero new code needed for the eval side.
- **`ReplaySession` stands in for a real MCP server**, so evaluation never
  touches Yahoo/OpenFIGI/your real database (`backend/ai/AGENTS.md` rule
  8). Any new "learn from real corrections" case must be captured as a
  static snapshot (what the tools returned *at the time*), the same shape
  `cases/*.json` already uses — not a live replay against today's data,
  which would make old cases silently drift as your portfolio changes.

`portfolio_assistant` and `import_reviewer` have no equivalent harness yet
— they're conversational/note-writing rather than "pick the one right
answer," so "accuracy" isn't as crisp a metric. §4.4 comes back to this.

---

## 3. What's missing: the "collect" half

Nothing today turns a real-world correction into a labeled example. Two
concrete sources of correction already exist in this app's UX, just
uncaptured:

- **`security_resolver`'s `flag_for_review` outcomes.** When the agent
  defers to a human (via the existing `AccountsPage` resolution review
  panel), the human's eventual choice of the *correct* listing is exactly
  the ground truth the agent didn't have — and it's already being decided
  and stored (as the final `asset_resolutions`/`assets` state), just never
  linked back to the `agent_runs` row that flagged it.
- **`portfolio_assistant` chat replies.** There's currently no way to tell
  the app "that answer was wrong/unhelpful" at all. A thumbs up/down per
  assistant message is the standard, cheap way to capture this — zero
  latency cost (it's a fire-and-forget write after the reply is already
  shown), and it's the single highest-value new UI surface this plan adds.

---

## 4. Closing the loop, phase by phase

### 4.1 Capture — a new `agent_feedback` table, written after the fact

```python
# backend/app/adapters/persistence/orm.py — new table
class AgentFeedbackORM(Base):
    """A human's correction or rating of one agent_runs row. Append-only —
    see docs/FEEDBACK_LOOPS_AND_FINE_TUNING.md. Never written on the
    request path that produced the run; always a later, separate call, so
    this has zero effect on chat/resolution latency."""

    __tablename__ = "agent_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    rating: Mapped[str] = mapped_column(String(10))  # 'up' | 'down' | 'corrected'
    correct_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # e.g. {"final_tool": "save_security_mapping", "symbol": "AAPL"}
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

```python
# backend/app/ports/feedback.py — one method, mirrors ResolutionRepo.add_agent_run's shape
class FeedbackRepo(Protocol):
    def add(self, agent_run_id: int, rating: str, correct_output: dict | None = None, note: str | None = None) -> None: ...
```

Wiring: a `POST /api/chat/messages/{id}/feedback` route (thumbs up/down —
looks up the `agent_runs` row via the message's stored run id) and, on the
existing resolution-review panel, capturing the human's final choice
whenever it resolves a row that has a `flag_for_review` `agent_runs` entry
sitting behind it — both are ordinary write endpoints, no different in
shape or cost from any other mutation already in `app/api/routes/`.

### 4.2 Turn corrections into eval cases — reuse `cases_local/`, don't invent a new mechanism

A tiny offline script, run by hand (or on the periodic cadence in §4.3),
converts `agent_feedback` rows with `rating='corrected'` (or `'down'` with
a follow-up note) into the exact JSON shape `security_resolver`'s
`cases/*.json` already uses, and drops them into `cases_local/`:

```python
# backend/ai/agents/security_resolver/evaluation/export_feedback_cases.py
"""Offline, run-by-hand: turns real corrected agent_runs rows into eval
cases (evaluation/cases_local/, gitignored — see backend/ai/AGENTS.md rule
8, these are real-holdings data, not the public-ISIN cases/ suite).
No effect on any request path; this only ever reads."""

def export(db: Session) -> int:
    rows = feedback_repo(db).list_corrections(agent="security_resolver")
    written = 0
    for row in rows:
        case = {
            "id": f"feedback-{row.agent_run_id}",
            "resolution": row.run.resolution_snapshot,  # captured at flag_for_review time, see §4.1's note
            "expected": row.correct_output,
        }
        (LOCAL_CASES_DIR / f"{case['id']}.json").write_text(json.dumps(case, indent=2), encoding="utf-8")
        written += 1
    return written
```

Once this exists, **`evaluate.py` needs zero changes** — `_load_cases()`
already merges `cases/` and `cases_local/`. This is the payoff of reusing
an existing mechanism instead of inventing a parallel "feedback case"
concept: the whole measurement pipeline (§2) now includes real corrections
for free.

### 4.3 Prompt iteration and periodic fine-tuning — both gated by the same harness

With real cases flowing in, two different levers become data-driven
instead of guesswork, and both are deliberately **not automatic**:

- **Prompt iteration.** Before/after any change to
  `ai/agents/security_resolver/prompt.md`, run
  `python -m ai.agents.security_resolver.evaluation.evaluate --model qwen3:8b`
  against the now-larger case suite and diff `final_accuracy`/
  `safety_violations`. This costs nothing beyond what already exists — the
  new cases just make the existing harness a better test.
- **Periodic fine-tuning (heavier, optional, do this only once §4.2 has
  produced a real batch of examples — dozens, not a handful).** Export the
  accumulated `agent_feedback`-derived cases as `(system_prompt, user_message,
  correct_tool_call)` triples, and either:
  - **Local path:** train a LoRA adapter on top of `qwen3:8b` from those
    triples (e.g. via `unsloth`/`peft` — outside this repo's scope to
    implement, but the important architectural point is where the output
    goes: a new Ollama model tag, e.g. `qwen3:8b-security-resolver-v2`,
    created via a `Modelfile` referencing the adapter).
  - **Hosted path:** if `AGENT_PROVIDER=openai` points at a provider with a
    fine-tuning API, submit the same triples there and get back a new
    model id.
  - **Promotion, either path:** run the new model through the **exact same
    `evaluate.py` harness**, and only flip `AGENT_MODEL` in `.env` to the
    new tag if it beats the current model on `final_accuracy` and
    `safety_violations` — never on latency alone, and never automatically.
    This is the one non-negotiable rule of this whole document: **a
    fine-tuned model is promoted by the same evaluated gate a hand-written
    prompt change goes through, nothing more automatic than that** — which
    is also exactly why this is not "online" fine-tuning (§1.4): there is
    always a human-reviewed evaluation step between "new data exists" and
    "production uses a different model."

Promotion itself costs *zero* code changes anywhere — `AGENT_MODEL` is
already a config value, not something hardcoded (root `AGENTS.md`'s
Phase 6 notes: "swap models/providers via `.env`, never code"). This
document doesn't add that property; it already existed, and it's exactly
why fine-tuning can stay this cheap to adopt later.

### 4.4 Extending this to `portfolio_assistant` and `import_reviewer`

Neither has a crisp "correct answer" the way `security_resolver` does
(`save X` vs. `flag`), so the harness needs a different metric, not the
same `final_accuracy` computation:

- **`portfolio_assistant`:** thumbs-down feedback (§4.1) is the label.
  Instead of "did it pick the right tool," an eval case becomes "given this
  conversation history + message, does the new model's reply *not* trigger
  the same down-vote pattern" — closer to an LLM-judge or a hand-written
  rubric per case than exact-match. This is a real added-complexity step;
  don't build it until §4.2's mechanism has produced enough down-voted
  transcripts to make a rubric worth writing.
- **`import_reviewer`:** a down-vote-equivalent is "the human dismissed
  the note as unhelpful/wrong" — `ai_notes.dismissed_at` already exists
  per `AiNoteORM`'s docstring; treat a dismissed note the same way
  `flag_for_review`'s human override is treated in §4.1, i.e. as a
  correction worth exporting.

Both are marked Phase 3 in §6 — worth building, but only after §4.1-4.3
prove the mechanism out on `security_resolver`, which already has the
clean binary-outcome shape this pattern was designed around.

### 4.5 Inference-time reflection — the one place §1.1 might actually pay for itself

`security_resolver`'s `save_security_mapping` call is the single highest-
stakes moment in this app's agent layer (it mutates real portfolio data).
It's the one plausible candidate for a self-check step: after the model
decides to save, one more tool-less call — "you're about to save candidate
X for resolution Y; here's why you chose it; does this still look right,
yes/no" — before actually invoking the tool. This doubles the model-call
cost of every save (§1.1's tax, paid on every run, not just failures), so
**do not add it speculatively** — add it only if `evaluate.py`'s
`safety_violations` metric, measured across the growing case suite from
§4.2, shows a real, non-zero rate worth paying that tax to reduce. This is
the same discipline `PLANNING_AND_DECOMPOSITION.md` §4 argues for planning:
measure the defect rate first, then decide if the latency tax is worth it,
never the other way around.

---

## 5. The loop, end to end

```
 real chat reply / real flag_for_review outcome
        │
        ▼
 human gives feedback (thumbs down, or picks the correct candidate)  ── §4.1, zero latency impact (after the fact)
        │
        ▼
 agent_feedback row, linked to the agent_runs row it corrects
        │
        ▼
 export_feedback_cases.py  (offline, run by hand / cron)              ── §4.2
        │
        ▼
 evaluation/cases_local/*.json   (same shape evaluate.py already reads)
        │
        ├──▶ python -m ...evaluate --model qwen3:8b   (prompt iteration) ── §4.3
        │
        └──▶ periodic fine-tune → new model tag → evaluate.py gate → promote via AGENT_MODEL  ── §4.3
        │
        ▼
 next real run uses the (possibly) improved model/prompt — same audit trail (agent_runs) as always
```

Every arrow in this diagram is either code that already exists
(`evaluate.py`, `cases_local/`, `AGENT_MODEL` as config) or a small,
additive piece this plan introduces. Nothing in this diagram sits on the
request path of a real chat message or resolution — that's the whole
point of choosing the batch/evaluated version over online fine-tuning.

---

## 6. Config additions

```python
# app/config.py — nothing here affects request latency; these only gate
# offline tooling and the new feedback-capture endpoints.
feedback_enabled: bool = False   # gates the thumbs-up/down UI + endpoint; off by default like every other agent feature flag here
```

No provider/model settings are needed beyond what `AGENT_MODEL`/
`AGENT_PROVIDER` already provide — a fine-tuned model is just a new value
for `AGENT_MODEL`.

---

## 7. Tests

- **Unit:** `FeedbackRepo.add` / a fake repo in `backend/tests/fakes.py`
  (mirroring the existing fakes for `resolution_repo`/`chat_repo`) —
  assert a feedback row links correctly to an `agent_runs` row.
- **Unit:** `export_feedback_cases.export` against a fake DB with a couple
  of `rating='corrected'` rows — assert the written JSON matches
  `cases/*.json`'s schema exactly (a schema drift here would silently
  break `evaluate.py`'s `_load_cases`, so this is worth pinning with a
  real assertion against one committed example case).
- **Evaluation, real model:** after the first real batch of exported
  cases, run `evaluate.py` once before touching anything, save the
  results file, then treat it as the baseline every future prompt/model
  change is diffed against — this *is* the test, run manually, not
  something `pytest` should automate (a real Ollama call is not a unit
  test — same reasoning as `backend/ai/AGENTS.md` rule 8's "public data
  only," extended to "the harness itself is a tool, not a CI gate").

---

## 8. Glossary (continuing `docs/AGENTS_AND_MCP_EXPLAINED.md`'s)

| Term | What it means | Where it lives here |
|---|---|---|
| Offline evaluation | Running a case suite against a real model, no training | `evaluate.py` (already built) |
| Feedback capture | Turning a real human correction into a stored, labeled record | `agent_feedback` table (§4.1) |
| SFT (supervised fine-tuning) | Training a model on (input, correct output) pairs | §4.3's local/hosted paths |
| DPO (direct preference optimization) | Training on (better, worse) output *pairs* rather than single correct answers | Not built here — thumbs up/down alone isn't a preference *pair*; would need capturing both the model's answer and a human-written better one |
| Online fine-tuning | Continuous weight updates from live traffic, no review gate | Deliberately **not** built — see §1.4 |
| Promotion gate | The evaluation step a new model/prompt must pass before it's used in production | `evaluate.py` + manual `AGENT_MODEL` swap (§4.3) |
| Reflection / self-critique | An extra inference-time check before finalizing an answer | §4.5, gated on measured need |

---

## 9. Phased implementation plan

1. **Phase 1 — capture only.** `agent_feedback` table + migration,
   `FeedbackRepo`, the thumbs up/down endpoint and UI on chat messages, and
   linking a resolution's human override back to its `agent_runs` row.
   Zero interaction with `evaluate.py` yet. This alone is useful — you get
   a real record of "the assistant was wrong here" even before anything
   consumes it.
2. **Phase 2 — the export script + `cases_local/` integration (§4.2).**
   Once Phase 1 has produced a handful of real corrections, write
   `export_feedback_cases.py` and confirm `evaluate.py` picks the new
   cases up with no changes on its side.
3. **Phase 3 — prompt-iteration discipline (§4.3, first half).** Start
   actually running the before/after evaluate.py comparison on prompt
   changes to `security_resolver`. This is process, not code — the
   "implementation" is just habit plus the two phases above.
4. **Phase 4 (only once Phase 2 has produced a real, non-trivial batch) —
   periodic fine-tuning + promotion gate (§4.3, second half).** The
   heaviest phase; don't start it on a handful of examples.
5. **Phase 5 (optional) — extend to `portfolio_assistant`/
   `import_reviewer` (§4.4)**, and only then, if the numbers justify it,
   the inference-time reflection step for `security_resolver` (§4.5).
