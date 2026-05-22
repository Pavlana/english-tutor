# CLAUDE.md — English Tutor

Working agreement for Claude Code on this repo. Read `docs/build_plan.md` for the phased roadmap and `docs/english_tutor_architecture.md` for the system design. This file is the *how*; the build plan is the *what/when*.

## What this is

A multi-agent English tutor (FastAPI backend) that runs structured learning sessions across Listening, Writing, Speaking, Grammar. An Orchestrator manages session state and routes to agents; a Feedback agent closes each session and seeds the next. Currently a personal, single-user tool; designed to become a standalone app.

**MVP scope (locked):** local, single-user, API-first (no UI), text-only speaking. Don't add auth, a frontend, Postgres, Docker, a vector DB, or an agent framework unless the build plan's Phase 9 explicitly calls for it.

## Environment & commands

- Python 3.12, managed with `uv`. Dependencies in `pyproject.toml`, locked in `uv.lock`.
- Secrets in `.env` (git-ignored): `ANTHROPIC_API_KEY`, `DATABASE_URL`. Never hardcode keys or model IDs.
- Run the API: `uv run uvicorn src.main:app --reload` — interactive docs at `/docs` (your manual test surface until there's a UI).
- Run tests: `uv run pytest`
- Lint/format (once added, Phase 0): `uv run ruff check .` and `uv run ruff format .`
- Walk a full session by hand (once added, Phase 4): `uv run python tools/repl.py`
- Add a dependency: `uv add <pkg>` (use `uv add --dev <pkg>` for dev tools). Do not edit `pyproject.toml` deps by hand.

## Current state (update as phases land)

Phase 0 complete. config.py (pydantic-settings, model tiers), AsyncAnthropic api_client (returns usage), observability.py (llm_calls logging), db/engine.py (SQLite). Phase 1 (persistence layer) is next.

## Architecture rules (enforce these)

- **Layering, no reaching around:** `routers` → `orchestrator` → `agents` → (`tools`, `db/repo`, `api_client`). Routers hold no business logic. Agents touch the DB *only* through `db/repo.py`. Nothing calls Anthropic except through `src/api_client.py`.
- **Agents are plain async functions**, not a framework. Each returns the shared `AgentResult` contract (`models.py`). No LangGraph/CrewAI/etc.
- **All state is in the DB.** There is no in-process session memory between HTTP requests — rehydrate from the DB each request. Turn counters (max-3-turns rule) live in the session record, not in variables. The orchestrator is a pure function of `(message, DB state)`.
- **Model tiering is mandatory.** Opus = planning/curriculum/topic gen/feedback synthesis. Sonnet = in-session delivery + evaluation. Haiku = high-frequency low-stakes (vocab extraction, answer checks, LLM-as-judge). Tier is chosen per call via config — never a hardcoded model string.
- **Observability from day one.** Every model call goes through `api_client`, which logs an `llm_calls` row (model, agent, session_id, input/output tokens, latency, cost, timestamp). If you add a code path that calls the model and skips this, it's a bug.
- **Prompts live in `src/prompts/`** as version-controlled files, imported not inlined, so prompt changes are reviewable diffs.
- **`session.tasks` is a JSON column**; vocabulary, learning logs, user profile are their own tables. Match the JSON shapes in architecture §9 exactly.

## Model IDs (single source of truth — use these strings, no others)

| Tier | Model ID |
|------|----------|
| opus | `claude-opus-4-7` |
| sonnet | `claude-sonnet-4-6` |
| haiku | `claude-haiku-4-5-20251001` |

These are the values that go into `config.py`. Never write a model string anywhere else. If Anthropic releases a new model, update this table and `config.py` — nowhere else.

## Testing & evals

- Evals are specced in `docs/english_tutor_evals.md`. Structural evals are pytest and ship **in the same PR as the agent they test** — never deferred.
- Three layers: structural (shape, every change), behavioural (code-based + LLM-as-judge, incremental), regression (after prompts stabilise).
- LLM-as-judge runs on Haiku. Log every eval result (input, output, pass/fail, timestamp) to `eval_results`.
- A task-status must never be left `in_progress` after an agent exits — it's `complete` or `skipped`.

## Code style

- **Type hints everywhere** — all function parameters and return types, including `-> None`. Use Python 3.12 syntax: `list[str]`, `dict[str, int]`, `str | None` (not `Optional`, not `List`).
- **Docstrings on all public functions and classes** — Google style. One line is fine for simple functions; use Args/Returns/Raises blocks when the function has non-trivial inputs, outputs, or failure modes.
- **Inline comments for non-obvious logic only** — explain *why*, not *what*. Do not comment self-evident code.
- **No magic numbers or magic strings** — name constants, don't inline them.
- **Async functions must be async all the way** — never call `asyncio.run()` inside a function that is already in an async context.

Ruff enforces all of the above — `ruff check .` will fail on missing docstrings (`D` rules, Google convention) and missing type annotations (`ANN` rules). A clean ruff check is required before any task is done.

Docstring example (follow this format):
```python
def get_open_session(user_id: str) -> Session | None:
    """Return the most recent in-progress session for a user, or None.

    Args:
        user_id: UUID string identifying the user.

    Returns:
        Session object if an open session exists, None otherwise.
    """
```

## Workflow

- One build-plan phase = one branch = one PR. Keep PRs to a single phase.
- Every PR ends `ruff`-clean and `pytest`-green.
- The student pedagogy (CEFR, gap queue, vocab recycling, mastery criteria) is in `docs/instructions_prompt.md` — agent prompts derive from it; don't reinvent the rules.
- After a phase lands, update "Current state" above and the build plan's phase status.

## Don't

- Don't introduce an agent framework, vector DB, Postgres, auth, or Docker before Phase 9, or a frontend before Phase 10.
- Don't call Anthropic outside `api_client`, or the DB outside `db/repo`.
- Don't inline prompts or hardcode model IDs.
- Don't use `os.getenv` directly anywhere — all config is read through the `Settings` object in `src/config.py` (pydantic-settings). One import, no scattered env reads.
- Don't edit a test to make it pass — if a test fails, fix the implementation. Tests are the spec; they are not negotiable.
- Don't mark a task `complete` with failing tests or partial implementation.
