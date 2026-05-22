# CLAUDE.md — English Tutor

Working agreement for Claude Code on this repo. Read `docs/build_plan.md` for the phased roadmap and `docs/english_tutor_architecture.md` for the system design. This file is the *how*; the build plan is the *what/when*.

## What this is

A multi-agent English tutor (FastAPI backend) that runs structured learning sessions across Listening, Writing, Speaking, Grammar. An Orchestrator manages session state and routes to agents; a Feedback agent closes each session and seeds the next. Currently a personal, single-user tool; designed to become a standalone app.

**MVP scope (locked):** local, single-user, API-first (no UI), text-only speaking. Don't add auth, a frontend, Postgres, Docker, a vector DB, or an agent framework unless the build plan's Phase 9 explicitly calls for it.

## Environment & commands

- Python 3.12, managed with `uv`. Dependencies in `pyproject.toml`, locked in `uv.lock`.
- Secrets in `.env` (git-ignored): `ANTHROPIC_API_KEY`, model IDs, `DATABASE_URL`. Never hardcode keys or model IDs.
- Run the API: `uv run uvicorn src.main:app --reload` — interactive docs at `/docs` (your manual test surface until there's a UI).
- Run tests: `uv run pytest`
- Lint/format (once added, Phase 0): `uv run ruff check .` and `uv run ruff format .`
- Walk a full session by hand (once added, Phase 4): `uv run python tools/repl.py`
- Add a dependency: `uv add <pkg>` (use `uv add --dev <pkg>` for dev tools). Do not edit `pyproject.toml` deps by hand.

## Current state (update as phases land)

Pre-implementation stub. `/health` returns ok. `/chat` makes one Anthropic call via `src/api_client.py` and returns text — no DB, no orchestrator, no agents, no tools, no token logging, no model tiering yet. Tests cover health + retry-on-500. **Phase 0 in the build plan is the next work.**

## Architecture rules (enforce these)

- **Layering, no reaching around:** `routers` → `orchestrator` → `agents` → (`tools`, `db/repo`, `api_client`). Routers hold no business logic. Agents touch the DB *only* through `db/repo.py`. Nothing calls Anthropic except through `src/api_client.py`.
- **Agents are plain async functions**, not a framework. Each returns the shared `AgentResult` contract (`models.py`). No LangGraph/CrewAI/etc.
- **All state is in the DB.** There is no in-process session memory between HTTP requests — rehydrate from the DB each request. Turn counters (max-3-turns rule) live in the session record, not in variables. The orchestrator is a pure function of `(message, DB state)`.
- **Model tiering is mandatory.** Opus = planning/curriculum/topic gen/feedback synthesis. Sonnet = in-session delivery + evaluation. Haiku = high-frequency low-stakes (vocab extraction, answer checks, LLM-as-judge). Tier is chosen per call via config — never a hardcoded model string.
- **Observability from day one.** Every model call goes through `api_client`, which logs an `llm_calls` row (model, agent, session_id, input/output tokens, latency, cost, timestamp). If you add a code path that calls the model and skips this, it's a bug.
- **Prompts live in `src/prompts/`** as version-controlled files, imported not inlined, so prompt changes are reviewable diffs.
- **`session.tasks` is a JSON column**; vocabulary, learning logs, user profile are their own tables. Match the JSON shapes in architecture §9 exactly.

## Testing & evals

- Evals are specced in `docs/english_tutor_evals.md`. Structural evals are pytest and ship **in the same PR as the agent they test** — never deferred.
- Three layers: structural (shape, every change), behavioural (code-based + LLM-as-judge, incremental), regression (after prompts stabilise).
- LLM-as-judge runs on Haiku. Log every eval result (input, output, pass/fail, timestamp) to `eval_results`.
- A task-status must never be left `in_progress` after an agent exits — it's `complete` or `skipped`.

## Workflow

- One build-plan phase = one branch = one PR. Keep PRs to a single phase.
- Every PR ends `ruff`-clean and `pytest`-green.
- The student pedagogy (CEFR, gap queue, vocab recycling, mastery criteria) is in `docs/instructions_prompt.md` — agent prompts derive from it; don't reinvent the rules.
- After a phase lands, update "Current state" above and the build plan's phase status.

## Don't

- Don't introduce an agent framework, vector DB, Postgres, auth, or Docker before Phase 9, or a frontend before Phase 10.
- Don't call Anthropic outside `api_client`, or the DB outside `db/repo`.
- Don't inline prompts or hardcode model IDs.
- Don't mark a task `complete` with failing tests or partial implementation.
