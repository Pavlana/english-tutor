# English Tutor — Build Plan

**Version:** 0.1 | **Date:** May 2026 | **Status:** Pre-implementation
**Scope decisions (locked for MVP):** Local, single-user · API-first (no UI yet) · Text-only speaking · Claude Code as pair programmer

This document turns `english_tutor_architecture.md` into a buildable sequence. It does not contain code. It defines the tech stack, corrects a few things in the architecture that don't survive contact with an HTTP server, and lays out phases that each end in something runnable and tested. Read it alongside `CLAUDE.md` (repo root), which is the working agreement for Claude Code.

---

## 1. The gap between where the code is and where it's going

The current repo is a stateless single-call stub: `/chat` → `call_anthropic` (one Sonnet-ish call, fixed model, 1024 tokens, 10s timeout, text-only) → `ChatResponse`. There is no DB, no orchestrator, no agents, no tools, no token logging, no model tiering.

The target is a 7-component, stateful, DB-backed system: Orchestrator + Onboarding + Listening + Writing + Speaking + Grammar + Feedback, each reading/writing persistent state, some calling external tools, with token usage logged per call.

That's a big gap. The plan closes it bottom-up: persistence and the orchestrator spine first (everything depends on them), then a thin vertical slice that runs end-to-end, then the remaining agents, then the feedback loop that makes the system actually progress between sessions.

---

## 2. Corrections to the architecture (read this before building)

These are places where the design document is either underspecified or wrong for the runtime it's going to live in. Decide on these now; they're expensive to retrofit.

**2.1 Short-term memory — persist the turn transcript, but in the DB, not in the HTTP round-trip.** The architecture's "in-memory, passed between agent calls" tier doesn't exist on an HTTP server: there's no persistent process memory between `/chat` requests, so each turn is a cold request.

The alternative you raised — carry the running transcript in the response (`user → assistant → user …`) and send it back each turn so the client holds the conversation — works mechanically (it's how the raw Messages API itself behaves) but is the wrong call here, for reasons that are **not** about tokens:

- **Token cost is identical either way.** What you pay for is the size of `system + messages` sent to the model on each call, independent of where that history was stored *between* requests. If turn 3 needs turns 1–2 as context, you resend them whether they came from the DB or from the request body. Persistence transport and token usage are orthogonal — don't choose DB vs client-carried on a token basis, because there is no token difference.
- **Durability/resumption forces the DB.** Architecture §6/§10 require resuming and saving partial state on abandonment. A client-held transcript is gone the moment the client dies (REPL killed, app restart, browser closed). You can't save partial state you don't hold.
- **You need the DB anyway** — vocabulary store, learning logs, profile, summaries, the 3-session grammar window, the token log. Client-carried transcript doesn't remove the DB; it splits session state across two places that can desync. Worse, not better.
- **Trust.** A client-authoritative transcript is client-tamperable (edit prior feedback, fake completion). Irrelevant single-user-local; a correctness/security hole multi-user.

**Recommendation:** persist the current task's turn transcript as a cheap appended JSON/text block on the `sessions` row — you were right that it should be a simple block, not a normalised turn table; just keep that block in the DB. The orchestrator becomes a pure function of `(incoming message, DB state)`, which also makes "resume an incomplete session" fall out for free. Turn counters (the max-3 rule) live in the session record. The real token lever is elsewhere and already in the design: **the orchestrator passes per-task *summaries* between agents, not raw transcripts** — sending full transcripts downstream would multiply input tokens across four agents. Add prompt caching for the repeated within-task prefix. Net: same tokens as client-carried, plus durability, resumption, and a single source of truth.

**2.2 Build the deterministic workflow orchestrator — that *is* the right call, and it is not an agent framework.** Your plan ("a workflow agent, all flows predictable") and this recommendation are the same thing under different names; the distinction worth making precise: because the flows are predictable, **routing is decided by code, not by a model.** The orchestrator is a deterministic graph of nodes (onboarding → listening → writing → speaking → grammar → feedback); the edges are `if`-logic over DB state; the LLM does the language work *inside* each node, never the control flow. That's a workflow engine you hand-roll in plain async Python with a shared `AgentResult` contract.

What to avoid is two specific things, not "workflows": (a) heavyweight frameworks (LangGraph/CrewAI/AutoGen) that add abstraction you'll fight and that bury the per-call token accounting the project wants; and (b) LLM-driven dynamic routing, where a model decides what runs next — unnecessary when the graph is fixed, and a source of nondeterminism in cost and behaviour. Hand-rolled deterministic workflow: yes. Framework or model-as-router: no.

**2.3 `api_client.py` must change before any agent is written.** Three problems for the target system: (a) it returns only `text` and throws away `response.usage`, but observability-from-day-one needs input/output tokens per call; (b) it has a single global `model`, but the project mandates Opus/Sonnet/Haiku tiering; (c) `max_tokens=1024` and a hard `10s` timeout will truncate or kill longer generations (exercise sets, summaries, feedback synthesis). Fix all three in Phase 0: a tier-aware async client that returns `(text, usage)` and is the *only* path to the model.

**2.4 `session.tasks` is a nested object — store it as a JSON column, not five join tables.** It's single-user, read and written as a unit, and shaped by the agent flow. A JSON column on the `sessions` row (SQLite has native JSON support) is the right call. Vocabulary, learning logs, and user profile are queried independently and get their own tables.

**2.5 Tier from evidence, not from the architecture's labels — and yes, Sonnet can likely take over planning later.** Two related points.

First, the Haiku tier is thinner than the design implies: most steps are Sonnet (delivery + evaluation) bookended by Opus (planning, topic generation, feedback synthesis). Genuine Haiku candidates are vocabulary extraction, comprehension-answer checking, and LLM-as-judge evals. Don't force Haiku where Sonnet is correct just to fill the tier.

Second, your instinct on using Sonnet for topic generation and curriculum is right and, more importantly, measurable. These are *constrained* tasks (given profile + learning log + last topic → next topic + focus), which is exactly where smaller models hold up; Opus's edge is on open-ended reasoning, not "pick the next logical step given these inputs." The method: (1) write the prompt with explicit criteria, a structured output schema, and 1–2 worked examples — model-agnostic, so the smaller model has enough scaffolding to succeed; (2) keep prompt (`src/prompts/`) and model (config) decoupled so the tier is a one-line swap; (3) gate the swap on an eval — the Feedback eval targets already cover most of it (next topic differs from current, is a logical progression, gap summary spans 3 sessions). Run Opus as the gold reference, run Sonnet against the same judge, downgrade when it passes. Do this in Phase 8, not now: early phases use Opus for correctness (only ~1–3 calls/session, trivial cost), and once the eval exists the swap is free to attempt.

**2.6 Model IDs live in config, never in code.** Current configured tiers (confirm at build time): `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`. The evals doc still references `claude-sonnet-4-20250514` — update it. One source of truth in settings.

---

## 3. Tech stack

Constraint: one developer, no funding, local single-user MVP, must stay cloud-portable. Default to what's already in `pyproject.toml`; add only what earns its place.

| Concern | Choice | Why / honest note |
|---|---|---|
| Language / runtime | Python 3.12 (already) | Keep. |
| Package manager | `uv` (already) | Keep. Fast, lockfile present. |
| Web framework | FastAPI + uvicorn (already) | Keep. Async-native, Swagger UI doubles as your manual test surface while there's no frontend. |
| Validation | Pydantic v2 (already) | Keep. Agent I/O contracts and DB models share it. |
| Config | **pydantic-settings** (add) | Replace raw `os.getenv`. Typed settings object holds model tiers, DB URL, API keys, per-tier `max_tokens`/timeouts. One import, no scattered env reads. |
| Database | **SQLite via SQLModel** (add) | SQLModel = Pydantic + SQLAlchemy, so models and schemas converge. SQLite is zero-infra and correct for single-user. Migration to Postgres later = change the engine URL. |
| Migrations | **Alembic, but not yet** | Use `create_all()` for MVP. Introduce Alembic at Phase 9 (cloud) once schemas stop moving. Adding it now is ceremony with no payoff. |
| LLM SDK | **AsyncAnthropic** (swap from sync+`to_thread`) | Native async fits FastAPI; drop the `asyncio.to_thread` wrapper. Keep returning the text block, but also return `usage`. |
| Retry | tenacity (already) | Keep. Widen the per-tier timeout; 10s is too tight for Opus planning/feedback. |
| YouTube search | **yt-dlp** (add) | Free, no API key, no quota. More fragile than the Data API but zero cost/setup; the fallback chain (Phase 4) contains the fragility. |
| Transcripts | **youtube-transcript-api** (add) | Free, no key. Load-bearing for vocab extraction + the "vocab appears in source" eval. Missing transcript → fall back to a reading article (next row), not straight to skip. |
| Article fallback | **Guardian Open Platform API** (free key) + **trafilatura** (add) | When no transcript: Guardian API (content search + body) is the reliable primary; Aeon via trafilatura text extraction; New Yorker best-effort (paywalled). Feeds the same vocab pipeline as video. |
| Dictionary | **dictionaryapi.dev** (Free Dictionary API) | Free, no key. Fallback: Wiktionary. |
| Observability | **SQLite `llm_calls` table + JSONL mirror** | No paid tooling. Every call logs model, agent, session_id, input/output tokens, latency, computed cost, timestamp. Cost analysis = SQL query or a notebook. (Optional later: Pydantic Logfire has a free tier and FastAPI auto-instrumentation — nice-to-have, not core.) |
| Dev interaction (no UI) | FastAPI `/docs` + **a tiny CLI REPL script** (add) | The REPL holds a `session_id` across turns so you can walk a full session by hand. This is your daily driver until Phase 9. |
| Testing | pytest + pytest-asyncio (already) + httpx | Structural evals are pytest, written alongside each agent (per `english_tutor_evals.md`). |
| Lint / format | **ruff** (add) | One tool, fast, format + lint. mypy optional. |
| CI | **GitHub Actions** (Phase 8) | Run ruff + structural evals on push. Free tier is plenty for one dev. |

**Explicitly NOT in the MVP stack:** any agent framework, any vector DB (RAG deferred until 500+ vocab words per the architecture), Postgres/Supabase (Phase 9), auth (single-user), a frontend framework (Phase 10, conditional), Docker (add at deploy, not before), STT/TTS (voice deferred).

---

## 4. Proposed repo structure

So Claude Code knows where things go. This is a target, not a mandate — adjust as it grows, but keep the layering (routers → orchestrator → agents → tools/repo → client).

```
english-tutor/
├── CLAUDE.md                  # working agreement for Claude Code (repo root, auto-read)
├── docs/                      # architecture, evals, instructions, this plan
├── src/
│   ├── main.py                # FastAPI app + /health (keep thin)
│   ├── config.py              # pydantic-settings: model tiers, DB URL, keys, timeouts
│   ├── api_client.py          # tier-aware async LLM client, returns (text, usage), logs every call
│   ├── models.py              # Pydantic request/response + AgentResult contract
│   ├── db/
│   │   ├── engine.py          # SQLModel engine + session
│   │   ├── schemas.py         # SQLModel tables: users, sessions, vocabulary, learning_logs, llm_calls, eval_results
│   │   └── repo.py            # the "DB read/write tools" — the ONLY way agents touch the DB
│   ├── orchestrator.py        # deterministic router / state machine
│   ├── agents/
│   │   ├── base.py            # shared task-agent loop (generate→turns→evaluate→summary→persist)
│   │   ├── onboarding.py
│   │   ├── listening.py
│   │   ├── writing.py
│   │   ├── speaking.py
│   │   ├── grammar.py
│   │   └── feedback.py
│   ├── tools/
│   │   ├── content.py         # acquisition fallback chain: yt-dlp + transcript → Guardian/Aeon article → topic-only
│   │   └── dictionary.py      # dictionaryapi.dev
│   ├── prompts/               # one file per agent prompt, version-controlled, imported not inlined
│   ├── observability.py       # cost calc + llm_calls writer (used by api_client)
│   └── routers/
│       └── chat.py            # thin: parse → orchestrator → response. No business logic.
├── tools/
│   └── repl.py                # CLI to walk a session by hand
└── tests/
    ├── test_chat.py           # existing
    ├── evals/                 # structural + behavioural, mirrors src/agents
    └── conftest.py            # in-memory SQLite fixture, mock LLM fixture
```

**Layering rule (enforce in review):** `routers` call `orchestrator`; `orchestrator` calls `agents`; `agents` call `tools` and `repo` and `api_client`; nothing reaches around a layer. Routers contain no business logic. Agents never touch the DB except through `repo`. Nothing calls Anthropic except through `api_client`.

---

## 5. Phases

Each phase is independently shippable, ends green (tests pass), and is one branch / one PR. The order de-risks by building the spine and persistence first, then **the Listening/content agent first among the agents** — it's the head of the data pipeline (vocabulary flows from it into Writing/Speaking/Grammar) *and* it carries the riskiest external dependency, so building it early answers the biggest open technical question (can content acquisition + fallback work reliably?) in week one instead of week six. Downstream agents are then built against real vocabulary, not a stub.

Vertical-slice milestones: **first end-to-end run at Phase 4** (onboarding → Listening → vocabulary). **Full closed loop at Phase 7** (feedback seeds the next session).

---

### Phase 0 — Foundation (no new features)

Make the existing call tiered, logged, and persistence-ready without changing observable behaviour.

- Add `config.py` (pydantic-settings): model tiers, per-tier `max_tokens` + timeout, `ANTHROPIC_API_KEY`, `DATABASE_URL`.
- Refactor `api_client.py`: `AsyncAnthropic`; accept a `tier`/`agent` arg; configurable `max_tokens`; return `(text, usage)`; widen timeouts per tier.
- Add `observability.py`: cost calc per model + writer for an `llm_calls` row on every call. Wire it into `api_client` so logging is impossible to forget.
- Add `db/engine.py` + SQLite; create the `llm_calls` table.
- Add `ruff`; keep existing tests green.

**Done when:** existing `/chat` and `/health` behave identically, every model call writes an `llm_calls` row, model tier is selectable via config. Tests: config loads; `llm_calls` row written with correct token counts (mock the SDK); existing tests still pass.

**CC tasks:** ① scaffold `config.py`; ② rewrite `api_client.py` (return usage + tier + async); ③ `observability.py` cost table + writer; ④ `db/engine.py` + `llm_calls` schema; ⑤ ruff config; ⑥ update `test_chat.py` for the new client signature.

---

### Phase 1 — Persistence layer

The data layer the whole system stands on. No LLM logic.

- `db/schemas.py`: `users`/`user_profile`, `sessions` (top-level columns + `tasks` JSON), `vocabulary`, `learning_logs`, `eval_results` (+ `llm_calls` from P0). Match the JSON shapes in architecture §9 exactly.
- `db/repo.py`: the functions the architecture calls "DB read/write tools" — `get_latest_learning_log(user_id)`, `get_open_session(user_id)`, `create_session`, `get_session`, `update_task`, `set_task_status`, `upsert_vocabulary`, `get_vocabulary_for_review`, `write_learning_log`, `get_user_profile`, `write_user_profile`, `get_grammar_history(user_id, last_n=3)`.
- `conftest.py`: in-memory SQLite fixture.

**Done when:** every repo function has a structural test (write→read round-trips, status transitions, "last 3 sessions" window correct). No LLM, no API.

**CC tasks:** ① SQLModel tables matching architecture §9; ② repo functions; ③ round-trip tests per function; ④ test fixture for ephemeral DB.

---

### Phase 2 — Orchestrator spine

The router from architecture §2, with stub agents so routing is testable end-to-end before any real agent exists.

- `models.py`: define `AgentResult` (message, agent name, task_status, db side-effects already applied, usage). This contract is frozen here — every agent returns it.
- `orchestrator.py`: implement the decision table — session-opening message → (learning log complete? → new session + topic; open session? → resume + surface progress; neither? → onboarding). Topic generation = Opus call.
- `agents/`: stub each agent returning a canned `AgentResult` so the orchestrator can route through a whole session.
- `routers/chat.py`: reduce to parse → `orchestrator.handle(request)` → response.

**Done when:** you can drive a full fake session through `/chat` (new-user → onboarding stub → each task stub → feedback stub → complete) and every routing branch is unit-tested as a decision table. Resume logic restores the right next task from DB.

**CC tasks:** ① `AgentResult` contract; ② orchestrator decision table; ③ topic-generation call (Opus); ④ stub agents; ⑤ thin `chat.py`; ⑥ routing tests for every branch incl. resume.

---

### Phase 3 — Onboarding flow

First real LLM flow; produces the profile + first learning log everything else reads.

- `agents/onboarding.py`: conversation agent (Sonnet), 4–6 exchanges, **turn count persisted to the session** (per correction §2.1). Silent evaluator (Opus) → user-profile JSON → `repo.write_user_profile`. Generate first learning log → `repo.write_learning_log`. Hand back to orchestrator → topic → session created.
- Prompts in `src/prompts/`, drawn from `instructions_prompt.md` (CEFR, gaps, interests).

**Done when:** a brand-new user completes onboarding over multiple turns and lands at session start with a persisted profile + learning log. Tests: profile has all architecture §9 fields; turn limit enforced; profile/log persisted; "onboarding runs once ever" (second time → routes to a session, not onboarding).

**CC tasks:** ① conversation agent w/ persisted turn state; ② silent evaluator → profile JSON; ③ first-log generation; ④ onboarding prompts; ⑤ structural evals; ⑥ once-only routing test.

---

### Phase 4 — Listening / content-acquisition agent (first agent) — **first vertical slice**

The head of the pipeline: it acquires content, extracts the vocabulary the other three agents consume, and runs comprehension. Built first because everything downstream depends on its vocabulary output, and because its external dependencies are the riskiest part of the system — prove them now, not in week six.

- `agents/base.py`: the shared turn-loop every task agent reuses — generate → user turns (max 3, counter in DB) → evaluate (acceptable → summary + "ready to move on?"; needs work → specific feedback + revision) → persist summary + `status` → return `AgentResult`. Listening's comprehension Q&A is its first consumer; Writing/Speaking/Grammar inherit it.
- `tools/content.py`: acquisition with a **fallback chain**, one stable output shape (`{source, text, url}`) regardless of modality:
  1. **YouTube** — search (yt-dlp) + transcript (youtube-transcript-api).
  2. No transcript → **reading article** on the same topic — Guardian Open Platform API (free key; content search + body) as primary; Aeon via trafilatura text extraction; New Yorker best-effort (often paywalled — optional).
  3. No article either → **topic-only**; downstream agents generate from internal knowledge.
- `agents/listening.py`: from acquired text → extract exactly 5 vocab (status `new`), 3–5 comprehension questions, evaluate answers (max 3 turns), summary; persist vocab + `status=complete` (or `skipped` only if it reached topic-only with no usable text).
- Evals: structural (5 vocab, 3–5 questions, status never left `in_progress`) + behavioural (each vocab word string-matches the source text; LLM-judge: questions answerable from the content, not general knowledge).

**Architecture note:** this merges part of the deferred Reading agent (architecture §11) into Listening as a fallback. Listening is now really a *content/comprehension* agent — video-first, article-fallback. Vocabulary acquisition (the pipeline-critical output) survives even when no video exists; only the listening-practice modality is lost in article mode. Update architecture §7/§11 to match.

**Done when:** onboarding → content acquisition (video or article) → 5 vocab + comprehension runs end-to-end through the REPL, and every fallback rung is exercised by a test (video, article, topic-only). This is the first usable slice.

**CC tasks:** ① `base.py` turn-loop w/ DB-backed turn state; ② `content.py` fallback chain (yt-dlp + transcript → Guardian/Aeon article → topic-only); ③ listening agent (vocab/questions/eval/summary); ④ source-match + judge evals; ⑤ fallback-rung tests; ⑥ prompts; ⑦ REPL walk-through.

---

### Phase 5 — Writing agent

Simplest task agent: pure LLM + dictionary, consuming the vocabulary Listening produced. Reuses `base.py` from Phase 4.

- `agents/writing.py`: essay task from topic + vocabulary; consume `learning_log_recommendations`. Vocab-usage assessment per word (used correctly / not used / used incorrectly) → persisted as signals for Feedback.
- `tools/dictionary.py`: first use of the dictionary tool.
- Evals (per `english_tutor_evals.md`): structural (prompt non-null, summary non-null after response, status `complete`, one vocab signal per word) + code-behavioural (≥2 vocab words appear in the task).

**Done when:** Listening → Writing runs with real vocabulary flowing between them, and vocab-usage signals are persisted for the Feedback agent.

**CC tasks:** ① writing agent on `base.py`; ② vocab-usage assessment + signal persistence; ③ dictionary tool; ④ writing prompts; ⑤ structural + vocab-coverage evals.

---

### Phase 6 — Speaking + Grammar agents

Reuse the Phase 4 turn-loop; differ only in task generation and what they read.

- `agents/speaking.py`: exactly 3 discussion questions, open-ended, each > ~10 words; same turn loop + vocab-usage signals like Writing.
- `agents/grammar.py`: exercises targeting gaps from learning log + `repo.get_grammar_history(last 3)`; ≥1 exercise per identified gap.
- Evals: structural for each + first **LLM-as-judge** evals (speaking questions open-ended/on-topic/distinct; grammar exercises actually target the gap). LLM-judge runs on Haiku.

**Done when:** all four task agents pass structural + ≥1 behavioural eval each, all reusing `base.py`.

**CC tasks:** ① speaking agent (3 Qs, length rule, vocab signals); ② grammar agent (3-session gap window); ③ LLM-judge harness (Haiku); ④ judge evals for both; ⑤ prompts.

---

### Phase 7 — Feedback agent + session completion — **closed loop**

The piece that makes the system progress instead of repeat.

- `agents/feedback.py`: triggered only when all four tasks `complete`/`skipped`. Update vocab status (`new`→`learning`→`mastered`) from Writing/Speaking signals; synthesise grammar gaps over the 3-session window; generate next learning log; `session.status = complete`; emit session summary to the user. Synthesis = Opus.
- Evals: log generated *only* when all tasks done; only current-session vocab modified; log has ≥1 grammar gap + vocab-to-review + next topic; next topic differs from current (code) and is topically related (judge); gap summary references last 3 sessions (judge).

**Done when:** session N's feedback produces the learning log that drives session N+1's topic and focus — verified by running two consecutive sessions through the REPL.

**CC tasks:** ① feedback trigger guard (all-tasks-done); ② vocab status transitions; ③ 3-session grammar synthesis; ④ next-log generation; ⑤ completion evals; ⑥ two-session continuity test.

---

### Phase 8 — Eval + observability hardening

Make regressions detectable and cost visible (the project's stated observability goal).

- `eval_results` logging (input, output, pass/fail, timestamp) for every eval — the architecture's minimum observability bar.
- Backfill behavioural evals where missing; add a regression suite now that prompts are stabilising.
- Cost view: query `llm_calls` for tokens/cost per session, per agent, per model. A notebook or a small `/admin/cost` endpoint.
- GitHub Actions: ruff + structural evals on every push.

**Done when:** CI is green on push, eval results are logged, and you can answer "what did the last session cost, broken down by agent and tier?" from data.

**CC tasks:** ① `eval_results` writer; ② regression suite; ③ cost query/endpoint; ④ GH Actions workflow.

---

### Phase 9 — Backend transition (Cowork → standalone) — deferred, do not pull forward

Only once the tutor logic is proven. Listed so earlier phases stay portable, not as near-term work. **No UI in this phase — see Phase 10.**

- SQLite → Postgres/Supabase: change engine URL; introduce Alembic; migrate schemas.
- Auth + per-user isolation, rate limiting, per-user cost attribution (see §6) — only if going multi-user.
- Deploy (Fly / Railway); Dockerfile; secrets management.
- RAG when vocab ≥ 500 words (architecture §4 trigger): Chroma local / pgvector deployed, VoyageAI embeddings.
- Remaining deferred features from architecture §11 (user-selectable order, word quiz). The article path of the Reading agent already lands in Phase 4.

---

### Phase 10 — UI — conditional, only if the solution proves out

Strictly after Phase 9 and gated on validation: build a frontend only once the backend tutor demonstrably works and is worth a UI. Until then the REPL + `/docs` are the interface. Minimal chat UI (HTMX or a small React page) over the existing API; no frontend framework commitment before this point.

---

## 6. Multi-user: what changes

The data *model* barely changes — `user_id` is already on every table, so the schema is multi-user-ready by design, and the DB-backed stateless-server design (§2.1) already scales horizontally (any worker handles any request because state lives in the DB). What changes is everything around it:

- **Auth & identity.** Real auth (OAuth/JWT or API keys); derive `user_id` from the verified token, never from the request body — `ChatRequest.user_id` is trivially spoofable today and must go.
- **Database engine.** SQLite's single-writer model serialises concurrent writes; move to Postgres/Supabase with connection pooling (the swap is Phase 9).
- **Rate limiting & cost control.** Each user now spends your Anthropic budget. Add per-user quotas, and add `user_id` to the `llm_calls` table **now** (cheap insurance) so per-user cost attribution works later.
- **Tools break at scale.** The free, keyless choices are single-user conveniences. yt-dlp gets throttled/IP-blocked by YouTube under load; free dictionary/article endpoints rate-limit; article scraping raises ToS questions across many users. Multi-user likely forces the official YouTube Data API (quota/paid), the Guardian API key (already the primary article path), and a cache keyed by topic so N users on one topic cost a single acquisition.
- **Secrets & ops.** `.env` → a secrets manager; observability becomes operational (alerting on error/cost spikes, not just a table you query).
- **Data handling.** Multiple people's learning data brings deletion/export and, for EU users, GDPR obligations.

Net: keep the schema as-is, add `user_id` to `llm_calls` now, and treat the free scraper tools as explicitly single-user — they're the first thing that breaks when you add the second user.

---

## 7. Cost model (framing, not a forecast)

Per-session LLM call inventory by tier — use it to sanity-check, then replace with real numbers from `llm_calls`:

- **Opus** (expensive, low-frequency): topic generation (1), onboarding evaluator (1, first session only), feedback synthesis (1). ~1–3 calls/session.
- **Sonnet** (workhorse): every task generation + per-turn evaluation across 4 agents. With up to 3 turns each, this dominates — roughly 8–20 calls/session.
- **Haiku** (cheap, high-frequency): vocab extraction, comprehension-answer checks, LLM-as-judge evals. Volume scales with eval coverage, not user turns.

The honest expectation from §2.5: Sonnet is the cost centre; Opus is a small fixed cost per session; Haiku is mostly an eval-time cost. Don't optimise tiers from theory — ship the `llm_calls` table in Phase 0 and let it tell you where a Sonnet call could be a Haiku one.

---

## 8. How Claude Code is used across this

`CLAUDE.md` at the repo root is the standing context (environment, commands, conventions, guardrails). This plan is the roadmap. The rhythm:

One phase = one branch = one PR. Within a phase, hand Claude Code the numbered CC tasks. Every agent ships with its structural evals in the same PR (per `english_tutor_evals.md`) — never "code now, evals later." Each PR ends with `ruff` clean and `pytest` green. Prompts live in `src/prompts/` as version-controlled files, never inlined, so prompt changes are reviewable diffs and the regression suite (Phase 8) has something stable to pin against.

Point Claude Code at this file's Phase section for *what* and *why*, and at `CLAUDE.md` for *how this repo works*. Keep both updated after each sprint — the architecture doc's own footer asks for it.

---

*Last updated: May 2026. Update after each phase.*
