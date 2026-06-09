"""Structural and behavioural evals for the listening agent.

All api_client and acquire_content calls are mocked — no real network or
model calls are made. Tests verify DB state after each scenario.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from src.agents.listening import run
from src.db.repo import create_session
from src.db.schemas import TutorSession, VocabularyItem
from src.tools.content import ContentResult

_CALL_ANTHROPIC: str = "src.agents.listening.call_anthropic"
_ACQUIRE_CONTENT: str = "src.agents.listening.acquire_content"

# ---------------------------------------------------------------------------
# Fixture text and canned model responses
# ---------------------------------------------------------------------------

_FIXTURE_TEXT: str = (
    "The stringent regulations imposed by the government have had a "
    "disproportionate impact on small businesses, prompting widespread calls "
    "for more equitable policies and a renewed focus on fiscal responsibility."
)

_FIXTURE_WORDS: list[str] = [
    "stringent",
    "disproportionate",
    "equitable",
    "fiscal responsibility",
    "widespread",
]

_FIXTURE_QUESTIONS: list[str] = [
    "What have the stringent regulations affected most?",
    "What have the widespread calls been demanding?",
    "What does 'disproportionate' suggest about the impact?",
]

_VOCAB_RESPONSE: tuple = (json.dumps(_FIXTURE_WORDS), None)
_QUESTIONS_RESPONSE: tuple = (json.dumps(_FIXTURE_QUESTIONS), None)
_EVAL_ACCEPTABLE: tuple = (
    json.dumps(
        {
            "acceptable": True,
            "feedback": "Good effort.",
            "summary": "Solid comprehension of the regulations topic.",
        }
    ),
    None,
)
_EVAL_UNACCEPTABLE: tuple = (
    json.dumps({"acceptable": False, "feedback": "Try again.", "summary": None}),
    None,
)
_SUMMARY_RESPONSE: tuple = ("A solid listening session on regulations.", None)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_content() -> ContentResult:
    """Return a Guardian ContentResult backed by the fixture text."""
    return ContentResult(
        source="Guardian",
        text=_FIXTURE_TEXT,
        url="https://example.com/article",
        modality="article",
    )


def _topic_only_content() -> ContentResult:
    """Return a topic-only ContentResult with no text."""
    return ContentResult(
        source="topic-only",
        text="",
        url=None,
        modality="topic-only",
    )


def _make_session(
    user_id: str, db_session: object, topic: str = "regulations"
) -> TutorSession:
    """Create a TutorSession with the given user_id and topic."""
    return create_session(user_id, topic, db_session)


# ===========================================================================
# STRUCTURAL EVALS
# ===========================================================================


@pytest.mark.asyncio
async def test_complete_run_writes_five_vocabulary_items(db_session: object) -> None:
    """After a complete run with text content, exactly 5 VocabularyItems exist."""
    user_id = "user-vocab-count"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,
            _QUESTIONS_RESPONSE,
            _EVAL_ACCEPTABLE,
            _SUMMARY_RESPONSE,
        ]
        await run("", ts, db_session)
        db_session.refresh(ts)
        await run("My answer.", ts, db_session)

    items = db_session.exec(
        select(VocabularyItem).where(VocabularyItem.user_id == user_id)
    ).all()
    assert len(items) == 5


@pytest.mark.asyncio
async def test_complete_run_sets_task_status_complete(db_session: object) -> None:
    """tasks["listening"]["status"] must be "complete" after the agent finishes."""
    user_id = "user-status-check"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,
            _QUESTIONS_RESPONSE,
            _EVAL_ACCEPTABLE,
            _SUMMARY_RESPONSE,
        ]
        await run("", ts, db_session)
        db_session.refresh(ts)
        result = await run("My answer.", ts, db_session)

    assert result.task_status == "complete"
    db_session.refresh(ts)
    tasks = json.loads(ts.tasks)
    assert tasks["listening"]["status"] == "complete"


@pytest.mark.asyncio
async def test_complete_run_stores_questions_list(db_session: object) -> None:
    """After run() with text, questions stored in DB parse to a list of 3–5 items."""
    user_id = "user-questions-check"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [_VOCAB_RESPONSE, _QUESTIONS_RESPONSE]
        await run("", ts, db_session)

    db_session.refresh(ts)
    tasks = json.loads(ts.tasks)
    questions = json.loads(tasks["listening"]["questions"])
    assert isinstance(questions, list)
    assert 3 <= len(questions) <= 5


@pytest.mark.asyncio
async def test_topic_only_skips_and_writes_no_vocabulary(db_session: object) -> None:
    """topic-only must set task_status="skipped" and write no VocabularyItems."""
    user_id = "user-topic-only"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock),
    ):
        mock_content.return_value = _topic_only_content()
        result = await run("", ts, db_session)

    assert result.task_status == "skipped"
    items = db_session.exec(
        select(VocabularyItem).where(VocabularyItem.user_id == user_id)
    ).all()
    assert len(items) == 0


# ===========================================================================
# SOURCE-MATCH EVAL
# ===========================================================================


@pytest.mark.asyncio
async def test_extracted_vocabulary_appears_verbatim_in_source_text(
    db_session: object,
) -> None:
    """All 5 extracted words must appear verbatim (case-insensitive) in source text."""
    user_id = "user-source-match"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [_VOCAB_RESPONSE, _QUESTIONS_RESPONSE]
        await run("", ts, db_session)

    items = db_session.exec(
        select(VocabularyItem).where(VocabularyItem.user_id == user_id)
    ).all()
    assert len(items) == 5
    source_lower = _FIXTURE_TEXT.lower()
    for item in items:
        assert item.word.lower() in source_lower, (
            f"Word '{item.word}' not found verbatim in source text"
        )


# ===========================================================================
# LLM-JUDGE EVALS
# ===========================================================================


@pytest.mark.asyncio
async def test_acceptable_evaluation_reaches_complete(db_session: object) -> None:
    """Judge returning acceptable=true must yield task_status='complete'."""
    user_id = "user-judge-accept"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,
            _QUESTIONS_RESPONSE,
            _EVAL_ACCEPTABLE,
            _SUMMARY_RESPONSE,
        ]
        await run("", ts, db_session)
        db_session.refresh(ts)
        result = await run("My answer.", ts, db_session)

    assert result.task_status == "complete"


@pytest.mark.asyncio
async def test_unacceptable_evaluation_returns_in_progress(db_session: object) -> None:
    """Judge returning acceptable=false must yield in_progress and increment turn."""
    user_id = "user-judge-reject"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,
            _QUESTIONS_RESPONSE,
            _EVAL_UNACCEPTABLE,
        ]
        await run("", ts, db_session)
        db_session.refresh(ts)
        result = await run("Bad answer.", ts, db_session)

    assert result.task_status == "in_progress"
    db_session.refresh(ts)
    assert ts.turn_count == 2


@pytest.mark.asyncio
async def test_max_turns_forces_complete(db_session: object) -> None:
    """At turn_count == 3 the agent forces complete regardless of the judge."""
    user_id = "user-max-turns"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,  # turn 0: vocab_extractor
            _QUESTIONS_RESPONSE,  # turn 0: question_generator
            _EVAL_UNACCEPTABLE,  # turn 1: answer_evaluator — rejected
            _EVAL_UNACCEPTABLE,  # turn 2: answer_evaluator — rejected
            _EVAL_UNACCEPTABLE,  # turn 3: answer_evaluator — forced complete
            _SUMMARY_RESPONSE,  # turn 3: listening_summary
        ]
        await run("", ts, db_session)  # turn 0 → in_progress
        db_session.refresh(ts)
        await run("Bad answer.", ts, db_session)  # turn 1 → in_progress
        db_session.refresh(ts)
        await run("Bad answer.", ts, db_session)  # turn 2 → in_progress
        db_session.refresh(ts)
        result = await run("Bad answer.", ts, db_session)  # turn 3 → force complete

    assert result.task_status == "complete"
