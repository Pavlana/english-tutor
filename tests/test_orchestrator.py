"""Routing tests for every branch of orchestrator.handle()."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.db.repo import create_session, update_task, write_learning_log
from src.models import AgentResult
from src.orchestrator import handle

# Patch targets — agents are imported as modules in orchestrator.py, so we
# patch the run attribute on each module via the orchestrator's namespace.
_ONBOARDING = "src.agents.onboarding.run"
_LISTENING = "src.agents.listening.run"
_WRITING = "src.agents.writing.run"
_FEEDBACK = "src.agents.feedback.run"
_GENERATE_TOPIC = "src.orchestrator.generate_topic"


def _result(agent: str) -> AgentResult:
    return AgentResult(message="stub", agent=agent, task_status="complete", usage=None)


def _all_tasks_complete(session_id: str, db_session) -> None:
    """Mark all four tasks complete on a session."""
    for task in ("listening", "writing", "speaking", "grammar"):
        update_task(session_id, task, {"status": "complete"}, db_session)


# ---------------------------------------------------------------------------
# Branch 1 — new user: no learning log, no open session → onboarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_user_routes_to_onboarding(db_session):
    with patch(_ONBOARDING, new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _result("onboarding")
        result = await handle("hi", "user-new", db_session)

    assert result.agent == "onboarding"
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Branch 2 — returning user: learning log exists, no open session → listening
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returning_user_generates_topic_and_starts_listening(db_session):
    write_learning_log("user-returning", None, {}, db_session)

    with (
        patch(_GENERATE_TOPIC, new_callable=AsyncMock) as mock_topic,
        patch(_LISTENING, new_callable=AsyncMock) as mock_run,
    ):
        mock_topic.return_value = "climate change"
        mock_run.return_value = _result("listening")
        result = await handle("hi", "user-returning", db_session)

    assert result.agent == "listening"
    mock_topic.assert_called_once()


@pytest.mark.asyncio
async def test_returning_user_creates_new_session_in_db(db_session):
    write_learning_log("user-session-check", None, {}, db_session)

    with (
        patch(_GENERATE_TOPIC, new_callable=AsyncMock) as mock_topic,
        patch(_LISTENING, new_callable=AsyncMock) as mock_run,
    ):
        mock_topic.return_value = "climate change"
        mock_run.return_value = _result("listening")
        await handle("hi", "user-session-check", db_session)

    # The listening agent receives a TutorSession as its second arg.
    _, call_kwargs = mock_run.call_args
    positional_args = mock_run.call_args[0]
    passed_session = positional_args[1]
    assert passed_session.topic == "climate change"


# ---------------------------------------------------------------------------
# Branch 3 — open session, all tasks not_started → first task (listening)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_session_all_not_started_routes_to_listening(db_session):
    create_session("user-open", "technology", db_session)

    with patch(_LISTENING, new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _result("listening")
        result = await handle("hi", "user-open", db_session)

    assert result.agent == "listening"
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Resume — listening complete, writing not_started → writing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_listening_done_routes_to_writing(db_session):
    ts = create_session("user-resume", "travel", db_session)
    update_task(ts.session_id, "listening", {"status": "complete"}, db_session)

    with patch(_WRITING, new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _result("writing")
        result = await handle("hi", "user-resume", db_session)

    assert result.agent == "writing"
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Resume — all tasks complete → feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tasks_complete_routes_to_feedback(db_session):
    ts = create_session("user-feedback", "science", db_session)
    _all_tasks_complete(ts.session_id, db_session)

    # Re-read to confirm tasks are persisted as complete before calling handle.
    tasks_raw = db_session.get(
        __import__("src.db.schemas", fromlist=["TutorSession"]).TutorSession,
        ts.session_id,
    ).tasks
    tasks = json.loads(tasks_raw)
    assert all(v["status"] == "complete" for v in tasks.values())

    with patch(_FEEDBACK, new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _result("feedback")
        result = await handle("hi", "user-feedback", db_session)

    assert result.agent == "feedback"
    mock_run.assert_called_once()
