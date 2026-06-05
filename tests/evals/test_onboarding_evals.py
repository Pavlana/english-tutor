"""Structural evals for the onboarding agent — novice and experienced paths.

All api_client calls are mocked — no real model calls are made.
Tests verify DB state after each path, not just return values.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.onboarding import (
    _MAX_CONVERSATION_TURNS,
    _evaluate_transcript,
    _experienced_path,
    _novice_path,
)
from src.db.repo import (
    create_session,
    get_latest_learning_log,
    get_user_profile,
    update_task,
)
from src.db.schemas import TutorSession

_CALL_ANTHROPIC = "src.agents.onboarding.call_anthropic"

# ---------------------------------------------------------------------------
# Canned LLM response payloads
# ---------------------------------------------------------------------------

_PROFILE_JSON: str = json.dumps(
    {
        "cefr_level": "B1",
        "vocabulary_range": "adequate everyday vocabulary",
        "grammar_gaps": ["present perfect"],
        "grammar_strengths": ["present simple"],
        "confidence_level": "medium",
        "interests": ["travel"],
        "onboarding_transcript": "User: I love travelling.",
    }
)

_LOG_JSON: str = json.dumps(
    {
        "vocabulary_to_review": [],
        "grammar_focus": ["present perfect"],
        "grammar_gap_summary": "Struggles with present perfect.",
        "session_notes": "B1 learner assessed via conversation.",
        "recommended_topic_tags": ["travel"],
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_onboarding_session(user_id: str, db_session) -> TutorSession:
    """Create a TutorSession with topic='onboarding'."""
    return create_session(user_id, "onboarding", db_session)


def _make_onboarding_session_with_transcript(user_id: str, db_session) -> TutorSession:
    """Create an onboarding session pre-seeded with a two-turn transcript."""
    ts = _make_onboarding_session(user_id, db_session)
    transcript = [
        {"role": "assistant", "content": "Tell me about yourself."},
        {"role": "user", "content": "I am Anna. I love to travel."},
    ]
    update_task(ts.session_id, "onboarding", {"transcript": transcript}, db_session)
    db_session.refresh(ts)
    return ts


# ===========================================================================
# NOVICE PATH
# ===========================================================================


@pytest.mark.asyncio
async def test_novice_path_writes_a1_profile(db_session) -> None:
    """_novice_path must write a UserProfile with cefr_level=A1."""
    ts = _make_onboarding_session("user-novice-profile", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.return_value = (_LOG_JSON, None)
        await _novice_path(ts, db_session)

    profile = get_user_profile("user-novice-profile", db_session)
    assert profile is not None
    assert profile["cefr_level"] == "A1"
    assert profile["assessment_method"] == "self_declared_novice"


@pytest.mark.asyncio
async def test_novice_path_writes_learning_log(db_session) -> None:
    """_novice_path must seed a LearningLog with generated_after_session=None."""
    ts = _make_onboarding_session("user-novice-log", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.return_value = (_LOG_JSON, None)
        await _novice_path(ts, db_session)

    log = get_latest_learning_log("user-novice-log", db_session)
    assert log is not None
    assert log["generated_after_session"] is None


@pytest.mark.asyncio
async def test_novice_path_makes_no_evaluator_or_conversation_calls(
    db_session,
) -> None:
    """_novice_path must never call the evaluator or conversation model."""
    ts = _make_onboarding_session("user-novice-nocalls", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.return_value = (_LOG_JSON, None)
        await _novice_path(ts, db_session)

    for call_args in mock_ca.call_args_list:
        agent_arg = call_args.kwargs.get("agent", "")
        assert agent_arg not in {"onboarding_evaluator", "onboarding_conversation"}


@pytest.mark.asyncio
async def test_novice_path_returns_complete_result(db_session) -> None:
    """_novice_path must return task_status='complete' and agent='onboarding'."""
    ts = _make_onboarding_session("user-novice-result", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.return_value = (_LOG_JSON, None)
        result = await _novice_path(ts, db_session)

    assert result.task_status == "complete"
    assert result.agent == "onboarding"


@pytest.mark.asyncio
async def test_novice_path_closes_session(db_session) -> None:
    """_novice_path must set session.status='complete' so orchestrator exits."""
    ts = _make_onboarding_session("user-novice-close", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.return_value = (_LOG_JSON, None)
        await _novice_path(ts, db_session)

    assert ts.status == "complete"


# ===========================================================================
# EXPERIENCED PATH — _evaluate_transcript
# ===========================================================================


@pytest.mark.asyncio
async def test_evaluate_transcript_writes_conversation_assessed_profile(
    db_session,
) -> None:
    """_evaluate_transcript must set assessment_method='conversation_assessed'."""
    ts = _make_onboarding_session_with_transcript("user-eval-profile", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.side_effect = [(_PROFILE_JSON, None), (_LOG_JSON, None)]
        await _evaluate_transcript(ts, db_session)

    profile = get_user_profile("user-eval-profile", db_session)
    assert profile is not None
    assert profile["assessment_method"] == "conversation_assessed"
    assert profile["cefr_level"] == "B1"


@pytest.mark.asyncio
async def test_evaluate_transcript_calls_opus_evaluator(db_session) -> None:
    """_evaluate_transcript must call Opus with agent='onboarding_evaluator'."""
    ts = _make_onboarding_session_with_transcript("user-eval-opus", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.side_effect = [(_PROFILE_JSON, None), (_LOG_JSON, None)]
        await _evaluate_transcript(ts, db_session)

    first_call = mock_ca.call_args_list[0]
    assert first_call.kwargs["tier"] == "opus"
    assert first_call.kwargs["agent"] == "onboarding_evaluator"


@pytest.mark.asyncio
async def test_evaluate_transcript_returns_complete_result(db_session) -> None:
    """_evaluate_transcript must return task_status='complete', agent='onboarding'."""
    ts = _make_onboarding_session_with_transcript("user-eval-result", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.side_effect = [(_PROFILE_JSON, None), (_LOG_JSON, None)]
        result = await _evaluate_transcript(ts, db_session)

    assert result.task_status == "complete"
    assert result.agent == "onboarding"


@pytest.mark.asyncio
async def test_evaluate_transcript_closes_session(db_session) -> None:
    """_evaluate_transcript must set session.status='complete'."""
    ts = _make_onboarding_session_with_transcript("user-eval-close", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.side_effect = [(_PROFILE_JSON, None), (_LOG_JSON, None)]
        await _evaluate_transcript(ts, db_session)

    assert ts.status == "complete"


@pytest.mark.asyncio
async def test_evaluate_transcript_writes_learning_log(db_session) -> None:
    """_evaluate_transcript must seed a LearningLog; generated_after_session=None."""
    ts = _make_onboarding_session_with_transcript("user-eval-log", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.side_effect = [(_PROFILE_JSON, None), (_LOG_JSON, None)]
        await _evaluate_transcript(ts, db_session)

    log = get_latest_learning_log("user-eval-log", db_session)
    assert log is not None
    assert log["generated_after_session"] is None


@pytest.mark.asyncio
async def test_evaluate_transcript_fallback_on_bad_json(db_session) -> None:
    """_evaluate_transcript must write A1 defaults when model returns invalid JSON."""
    ts = _make_onboarding_session_with_transcript("user-eval-fallback", db_session)
    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.side_effect = [("not valid json {{", None), (_LOG_JSON, None)]
        await _evaluate_transcript(ts, db_session)

    profile = get_user_profile("user-eval-fallback", db_session)
    assert profile is not None
    assert profile["cefr_level"] == "A1"


# ===========================================================================
# EXPERIENCED PATH — turn flow
# ===========================================================================


@pytest.mark.asyncio
async def test_experienced_path_evaluates_at_max_turns(db_session) -> None:
    """_experienced_path must trigger evaluation when turn_count reaches the max."""
    ts = _make_onboarding_session_with_transcript("user-exp-maxturn", db_session)
    ts.turn_count = _MAX_CONVERSATION_TURNS
    db_session.add(ts)
    db_session.commit()

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.side_effect = [(_PROFILE_JSON, None), (_LOG_JSON, None)]
        result = await _experienced_path("last message", ts, db_session)

    assert result.task_status == "complete"
    assert result.agent == "onboarding"


@pytest.mark.asyncio
async def test_experienced_path_conversation_uses_sonnet(db_session) -> None:
    """Mid-conversation turns must call Sonnet, agent='onboarding_conversation'."""
    ts = _make_onboarding_session_with_transcript("user-exp-sonnet", db_session)
    ts.turn_count = 2
    db_session.add(ts)
    db_session.commit()

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.return_value = ("Great! Tell me more.", None)
        result = await _experienced_path("I am from Brazil.", ts, db_session)

    first_call = mock_ca.call_args_list[0]
    assert first_call.kwargs["tier"] == "sonnet"
    assert first_call.kwargs["agent"] == "onboarding_conversation"
    assert result.task_status == "in_progress"


@pytest.mark.asyncio
async def test_experienced_path_turn_count_does_not_exceed_max(db_session) -> None:
    """turn_count must never exceed _MAX_CONVERSATION_TURNS before evaluation fires."""
    ts = _make_onboarding_session_with_transcript("user-exp-turncount", db_session)
    ts.turn_count = 2
    db_session.add(ts)
    db_session.commit()

    with patch(_CALL_ANTHROPIC, new_callable=AsyncMock) as mock_ca:
        mock_ca.return_value = ("Tell me more.", None)
        await _experienced_path("Hello!", ts, db_session)

    db_session.refresh(ts)
    assert ts.turn_count <= _MAX_CONVERSATION_TURNS
