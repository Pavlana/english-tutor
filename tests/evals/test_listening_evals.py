"""Structural and behavioural evals for the listening agent.

All api_client and acquire_content calls are mocked — no real network or
model calls are made. Tests verify DB state after each scenario.

Call sequence per turn (turns 1+):
  intent_classifier (haiku) → answer_evaluator (haiku) [on answer attempt]
  intent_classifier (haiku) → help_responder (haiku/sonnet) [on help request]
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

_INTENT_ANSWER: tuple = (json.dumps({"intent": "answer"}), None)
_INTENT_VOCAB: tuple = (json.dumps({"intent": "vocabulary"}), None)
_INTENT_QUIT: tuple = (json.dumps({"intent": "session_quit"}), None)

_EVAL_ACCEPTABLE: tuple = (
    json.dumps(
        {
            "acceptable": True,
            "questions_addressed": [1, 2, 3],
            "feedback": "Good effort.",
            "summary": "Solid comprehension of the regulations topic.",
        }
    ),
    None,
)
_EVAL_UNACCEPTABLE: tuple = (
    json.dumps(
        {
            "acceptable": False,
            "questions_addressed": [1],
            "feedback": "Try again.",
            "summary": None,
        }
    ),
    None,
)
# Quit with enough questions addressed (≥ _MIN_QUESTIONS_TO_QUIT).
_EVAL_QUIT_ENOUGH: tuple = (
    json.dumps(
        {
            "acceptable": False,
            "questions_addressed": [1, 2, 3],
            "feedback": "You addressed 3 questions — good effort on those.",
            "summary": None,
        }
    ),
    None,
)
# Quit with too few questions addressed (< _MIN_QUESTIONS_TO_QUIT).
_EVAL_QUIT_TOO_FEW: tuple = (
    json.dumps(
        {
            "acceptable": False,
            "questions_addressed": [1, 2],
            "feedback": "You've only addressed 2 questions so far.",
            "summary": None,
        }
    ),
    None,
)
_SUMMARY_RESPONSE: tuple = ("A solid listening session on regulations.", None)
_HELP_RESPONSE: tuple = (
    "Stringent means very strict or demanding. "
    "Give the questions another go when you're ready.",
    None,
)

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
            _VOCAB_RESPONSE,  # turn 0: vocab_extractor
            _QUESTIONS_RESPONSE,  # turn 0: question_generator
            _INTENT_ANSWER,  # turn 1: intent_classifier
            _EVAL_ACCEPTABLE,  # turn 1: answer_evaluator
            _SUMMARY_RESPONSE,  # turn 1: listening_summary
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
            _INTENT_ANSWER,
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
            _INTENT_ANSWER,
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
            _INTENT_ANSWER,
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
    """At turn_count == _MAX_ANSWER_ATTEMPTS the agent forces complete."""
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
            _INTENT_ANSWER,  # turn 1: intent_classifier
            _EVAL_UNACCEPTABLE,  # turn 1: answer_evaluator — rejected, count→2
            _INTENT_ANSWER,  # turn 2: intent_classifier — force complete
            _EVAL_UNACCEPTABLE,  # turn 2: answer_evaluator — force complete
            _SUMMARY_RESPONSE,  # turn 2: listening_summary
        ]
        await run("", ts, db_session)  # turn 0 → in_progress
        db_session.refresh(ts)
        await run("Bad answer.", ts, db_session)  # turn 1 → in_progress
        db_session.refresh(ts)
        result = await run("Bad answer.", ts, db_session)  # turn 2 → force complete

    assert result.task_status == "complete"


# ===========================================================================
# GATE EVALS — empty message and help requests
# ===========================================================================


@pytest.mark.asyncio
async def test_empty_message_returns_in_progress_no_turn_increment(
    db_session: object,
) -> None:
    """Empty message must return in_progress and not increment turn_count."""
    user_id = "user-empty-msg"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [_VOCAB_RESPONSE, _QUESTIONS_RESPONSE]
        await run("", ts, db_session)  # turn 0 → task card shown, turn_count=1
        db_session.refresh(ts)
        assert ts.turn_count == 1

        result = await run("", ts, db_session)  # empty → nudge, no increment

    assert result.task_status == "in_progress"
    db_session.refresh(ts)
    assert ts.turn_count == 1  # unchanged


@pytest.mark.asyncio
async def test_help_request_returns_in_progress_no_turn_increment(
    db_session: object,
) -> None:
    """A vocabulary help request must return in_progress without incrementing turn."""
    user_id = "user-help-vocab"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,  # turn 0: vocab_extractor
            _QUESTIONS_RESPONSE,  # turn 0: question_generator
            _INTENT_VOCAB,  # turn 1: intent_classifier → vocabulary
            _HELP_RESPONSE,  # turn 1: help_responder
        ]
        await run("", ts, db_session)  # turn 0 → task card, turn_count=1
        db_session.refresh(ts)
        result = await run("what does stringent mean?", ts, db_session)

    assert result.task_status == "in_progress"
    db_session.refresh(ts)
    assert ts.turn_count == 1  # help must not consume an attempt


@pytest.mark.asyncio
async def test_help_then_answer_reaches_complete(db_session: object) -> None:
    """A help request followed by a valid answer must complete the task."""
    user_id = "user-help-then-answer"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,  # turn 0: vocab_extractor
            _QUESTIONS_RESPONSE,  # turn 0: question_generator
            _INTENT_VOCAB,  # turn 1: intent_classifier → vocabulary
            _HELP_RESPONSE,  # turn 1: help_responder
            _INTENT_ANSWER,  # turn 1 (retry): intent_classifier → answer
            _EVAL_ACCEPTABLE,  # turn 1 (retry): answer_evaluator
            _SUMMARY_RESPONSE,  # turn 1 (retry): listening_summary
        ]
        await run("", ts, db_session)
        db_session.refresh(ts)
        await run("what does stringent mean?", ts, db_session)  # help, no increment
        db_session.refresh(ts)
        result = await run("Small businesses were most affected.", ts, db_session)

    assert result.task_status == "complete"


# ===========================================================================
# GATE EVALS — session quit
# ===========================================================================


@pytest.mark.asyncio
async def test_quit_with_enough_questions_completes(db_session: object) -> None:
    """Quit with questions_addressed >= _MIN_QUESTIONS_TO_QUIT must complete."""
    user_id = "user-quit-enough"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,  # turn 0: vocab_extractor
            _QUESTIONS_RESPONSE,  # turn 0: question_generator
            _INTENT_QUIT,  # turn 1: intent_classifier → session_quit
            _EVAL_QUIT_ENOUGH,  # turn 1: answer_evaluator (3 addressed)
            _SUMMARY_RESPONSE,  # turn 1: listening_summary
        ]
        await run("", ts, db_session)
        db_session.refresh(ts)
        result = await run("I want to stop here.", ts, db_session)

    assert result.task_status == "complete"


@pytest.mark.asyncio
async def test_quit_with_too_few_questions_stays_in_progress(
    db_session: object,
) -> None:
    """Quit with questions_addressed < _MIN_QUESTIONS_TO_QUIT must stay in_progress."""
    user_id = "user-quit-too-few"
    ts = _make_session(user_id, db_session)

    with (
        patch(_ACQUIRE_CONTENT, new_callable=AsyncMock) as mock_content,
        patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca,
    ):
        mock_content.return_value = _text_content()
        mock_ca.side_effect = [
            _VOCAB_RESPONSE,  # turn 0: vocab_extractor
            _QUESTIONS_RESPONSE,  # turn 0: question_generator
            _INTENT_QUIT,  # turn 1: intent_classifier → session_quit
            _EVAL_QUIT_TOO_FEW,  # turn 1: answer_evaluator (2 addressed)
        ]
        await run("", ts, db_session)
        db_session.refresh(ts)
        result = await run("I give up.", ts, db_session)

    assert result.task_status == "in_progress"
    db_session.refresh(ts)
    assert ts.turn_count == 1  # quit attempt must not consume an answer turn
