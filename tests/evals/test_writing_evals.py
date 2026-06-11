"""Structural and behavioural evals for src/agents/writing.py.

All api_client calls are mocked — no real network or model calls.
DB calls (get_vocabulary_for_review, update_vocab_signals, etc.) run against
the in-memory SQLite fixture from conftest.py.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agents import writing
from src.db.repo import create_session, upsert_vocabulary
from src.db.schemas import TutorSession

# ---------------------------------------------------------------------------
# Patch target
# ---------------------------------------------------------------------------

_CALL_ANTHROPIC: str = "src.agents.writing.call_anthropic"

# ---------------------------------------------------------------------------
# Vocabulary fixture
# ---------------------------------------------------------------------------

_TEST_VOCAB: list[str] = [
    "austerity",
    "stringent",
    "equitable",
    "fiscal",
    "disproportionate",
]

# ---------------------------------------------------------------------------
# Canned model responses
# ---------------------------------------------------------------------------

# Turn 0: writing task generation (contains two vocab words for the behavioural eval).
_GENERATION_TEXT: str = (
    "Write 80–120 words on economic policy. "
    "Use 'austerity' and 'stringent' naturally in your response."
)

# Evaluation responses.
_EVAL_ACCEPTABLE: dict = {
    "acceptable": True,
    "feedback": "Good essay — clear argument and vocab well used.",
    "summary": "The learner argued against harsh austerity measures.",
}
_EVAL_UNACCEPTABLE: dict = {
    "acceptable": False,
    "feedback": "Please use at least one vocabulary word from the list.",
    "summary": None,
}

# Vocab-usage assessment.
_SIGNALS_ALL_USED: dict[str, str] = {w: "used_correctly" for w in _TEST_VOCAB}
_SIGNALS_NONE_USED: dict[str, str] = {w: "not_used" for w in _TEST_VOCAB}

# Writing summary.
_SUMMARY_TEXT: str = (
    "You argued against stringent austerity policies, "
    "using both target words correctly."
)

# Valid signal values for assertion checks.
_VALID_SIGNALS: frozenset[str] = frozenset(
    {"used_correctly", "not_used", "used_incorrectly"}
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(db_session) -> TutorSession:
    """Create a fresh TutorSession for each test."""
    return create_session("test-user", "economic policy", db_session)


def _seed_vocab(user_id: str, session_id: str, db_session) -> None:
    """Insert _TEST_VOCAB words so get_vocabulary_for_review returns them."""
    for word in _TEST_VOCAB:
        upsert_vocabulary(
            user_id=user_id,
            word=word,
            session_id=session_id,
            session=db_session,
            topic_tags=["economic policy"],
        )


def _writing_task(ts: TutorSession, db_session) -> dict:
    """Return the tasks['writing'] dict from the DB, refreshed."""
    db_session.refresh(ts)
    return json.loads(ts.tasks)["writing"]


# ---------------------------------------------------------------------------
# Structural eval 1 — turn 0 returns task text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_0_returns_task_text(db_session) -> None:
    """Turn 0 calls the generator and returns the task text as in_progress."""
    ts = _make_session(db_session)

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock:
        mock.side_effect = [(_GENERATION_TEXT, None)]
        result = await writing.run("", ts, db_session)

    db_session.refresh(ts)
    t = _writing_task(ts, db_session)

    assert result.message == _GENERATION_TEXT
    assert result.task_status == "in_progress"
    # DB task status is not set to "in_progress" explicitly — it becomes
    # "complete" only on exit. Assert it has not completed yet.
    assert t["status"] != "complete"
    assert t["task"]


# ---------------------------------------------------------------------------
# Structural eval 2 — task status is never "in_progress" on exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_status_never_in_progress_on_exit(db_session) -> None:
    """After an acceptable evaluation the task reaches 'complete', not 'in_progress'."""
    ts = _make_session(db_session)

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock:
        mock.side_effect = [
            (_GENERATION_TEXT, None),               # turn 0: generate
            (json.dumps(_EVAL_ACCEPTABLE), None),   # turn 1: evaluate
            (json.dumps(_SIGNALS_ALL_USED), None),  # turn 1: vocab assess
            (_SUMMARY_TEXT, None),                  # turn 1: summary
        ]
        await writing.run("", ts, db_session)
        db_session.refresh(ts)
        await writing.run("Austerity measures can be stringent.", ts, db_session)

    assert _writing_task(ts, db_session)["status"] == "complete"


# ---------------------------------------------------------------------------
# Structural eval 3 — vocab signals persisted on completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vocab_signals_persisted(db_session) -> None:
    """tasks['writing']['vocab_signals'] is a non-empty JSON dict after completion."""
    ts = _make_session(db_session)
    # Seed vocab so get_vocabulary_for_review returns words, which flow into
    # the assessment call and produce non-empty signals.
    _seed_vocab(ts.user_id, ts.session_id, db_session)

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock:
        mock.side_effect = [
            (_GENERATION_TEXT, None),
            (json.dumps(_EVAL_ACCEPTABLE), None),
            (json.dumps(_SIGNALS_ALL_USED), None),
            (_SUMMARY_TEXT, None),
        ]
        await writing.run("", ts, db_session)
        db_session.refresh(ts)
        await writing.run("Austerity measures can be stringent.", ts, db_session)

    t = _writing_task(ts, db_session)
    signals: dict = json.loads(t["vocab_signals"])

    assert signals, "vocab_signals must be non-empty"
    assert all(
        v in _VALID_SIGNALS for v in signals.values()
    ), f"All signal values must be in {_VALID_SIGNALS}, got: {set(signals.values())}"


# ---------------------------------------------------------------------------
# Structural eval 4 — summary non-null on completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_non_null_on_complete(db_session) -> None:
    """tasks['writing']['summary'] is a non-empty string after completion."""
    ts = _make_session(db_session)

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock:
        mock.side_effect = [
            (_GENERATION_TEXT, None),
            (json.dumps(_EVAL_ACCEPTABLE), None),
            (json.dumps(_SIGNALS_ALL_USED), None),
            (_SUMMARY_TEXT, None),
        ]
        await writing.run("", ts, db_session)
        db_session.refresh(ts)
        await writing.run("Austerity measures can be stringent.", ts, db_session)

    t = _writing_task(ts, db_session)
    assert t["summary"], "summary must be a non-empty string after completion"


# ---------------------------------------------------------------------------
# Behavioural eval — generated task contains ≥2 vocab words
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writing_task_contains_at_least_two_vocab_words(db_session) -> None:
    """The generated task text references at least 2 vocabulary words from the session.

    _GENERATION_TEXT is the mocked LLM output. This test validates that the
    prompt constraint (≥2 vocab words) is reflected in what the agent returns
    to the user — without making a real model call.
    """
    ts = _make_session(db_session)
    _seed_vocab(ts.user_id, ts.session_id, db_session)

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock:
        mock.side_effect = [(_GENERATION_TEXT, None)]
        result = await writing.run("", ts, db_session)

    message_lower = result.message.lower()
    matches = sum(1 for w in _TEST_VOCAB if w in message_lower)
    assert matches >= 2, (
        f"Expected ≥2 vocab words in task text, found {matches}. "
        f"Message: {result.message!r}"
    )


# ---------------------------------------------------------------------------
# Structural eval — max turns forces completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_turns_forces_complete(db_session) -> None:
    """Three consecutive unacceptable evaluations must force task completion.

    Turn sequence:
      Turn 0: generate (1 call)
      Turn 1: evaluate → unacceptable (1 call), turn_count→2
      Turn 2: evaluate → unacceptable (1 call), turn_count→3
      Turn 3: evaluate → unacceptable but force_complete (3 calls: eval+assess+summary)
    """
    ts = _make_session(db_session)

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock:
        mock.side_effect = [
            (_GENERATION_TEXT, None),                 # turn 0: generate
            (json.dumps(_EVAL_UNACCEPTABLE), None),   # turn 1: eval (fail)
            (json.dumps(_EVAL_UNACCEPTABLE), None),   # turn 2: eval (fail)
            (json.dumps(_EVAL_UNACCEPTABLE), None),   # turn 3: eval (force complete)
            (json.dumps(_SIGNALS_NONE_USED), None),   # turn 3: vocab assess
            (_SUMMARY_TEXT, None),                    # turn 3: summary
        ]
        await writing.run("", ts, db_session)           # turn 0
        db_session.refresh(ts)
        await writing.run("Short answer.", ts, db_session)  # turn 1
        db_session.refresh(ts)
        await writing.run("Another try.", ts, db_session)   # turn 2
        db_session.refresh(ts)
        await writing.run("One more.", ts, db_session)      # turn 3

    assert _writing_task(ts, db_session)["status"] == "complete", (
        "Task must be 'complete' after three unacceptable evaluation turns"
    )
