"""Orchestrator — session manager and router.

Implements the deterministic decision table that decides which agent handles
each incoming message. This is a pure function of (message, DB state); it
holds no in-memory state between HTTP requests.
"""

import json
from types import ModuleType

from sqlmodel import Session as DBSession

from src.agents import feedback, grammar, listening, onboarding, speaking, writing
from src.db.repo import (
    create_session,
    get_latest_learning_log,
    get_open_session,
)
from src.db.schemas import TutorSession
from src.models import AgentResult
from src.topic_generator import generate_topic

_TASK_ORDER: list[str] = ["listening", "writing", "speaking", "grammar"]

# Shown to the user at the start of every new learning session (Branch 2).
_NEW_SESSION_GREETING: str = "Let's start a new session!"
_RESUMABLE_STATUSES: frozenset[str] = frozenset({"not_started", "in_progress"})

# Keyed by task name → agent module. `.run` is looked up at dispatch time so
# that patching the module attribute in tests intercepts the call correctly.
_TASK_MODULES: dict[str, ModuleType] = {
    "listening": listening,
    "writing": writing,
    "speaking": speaking,
    "grammar": grammar,
}


def _next_task(tasks: dict) -> str | None:
    """Return the name of the first resumable task, or None if all are terminal.

    Args:
        tasks: Parsed tasks dict from TutorSession.tasks JSON column.

    Returns:
        Task name string if a resumable task exists, otherwise None.
    """
    for name in _TASK_ORDER:
        if tasks.get(name, {}).get("status") in _RESUMABLE_STATUSES:
            return name
    return None


async def handle(
    message: str,
    user_id: str,
    db_session: DBSession,
) -> AgentResult:
    """Route an incoming message to the correct agent based on DB state.

    Decision table (evaluated in order):
      1. Open session exists → resume next incomplete task, or dispatch to
         feedback agent if all tasks are terminal.
      2. No open session but a learning log exists → generate a topic, open a
         new session, dispatch to listening agent.
      3. No open session and no learning log → dispatch to onboarding agent.

    This function contains no LLM calls. All model work is delegated to the
    agent or topic_generator it dispatches to.

    Args:
        message: Raw text sent by the user.
        user_id: Identifies the user making the request.
        db_session: Active database session for all DB reads/writes.

    Returns:
        AgentResult produced by the dispatched agent.
    """
    # ── Branch 1: resume an open session ────────────────────────────────────
    open_ts = get_open_session(user_id, db_session)
    if open_ts is not None:
        # Onboarding uses a dedicated session (topic="onboarding") to track
        # turn state before a learning log or real session exists.
        if open_ts.topic == "onboarding":
            return await onboarding.run(message, open_ts, db_session)

        tasks = json.loads(open_ts.tasks)
        next_task_name = _next_task(tasks)

        if next_task_name is None:
            return await feedback.run(message, open_ts, db_session)

        result = await _TASK_MODULES[next_task_name].run(message, open_ts, db_session)

        # Auto-advance: if the task just completed, immediately start the next
        # one so the user sees the transition in a single response instead of
        # having to send an empty "okay" to trigger the next task's turn 0.
        # Guard: only chain when the DB actually moved forward (real agent
        # updated the status). Mocked agents in tests don't touch the DB, so
        # next_next == next_task_name there — no chain, no infinite loop.
        if result.task_status == "complete":
            db_session.refresh(open_ts)
            tasks_after = json.loads(open_ts.tasks)
            next_next = _next_task(tasks_after)
            if next_next is not None and next_next != next_task_name:
                next_result = await _TASK_MODULES[next_next].run(
                    "", open_ts, db_session
                )
                next_result.message = (
                    f"{result.message}\n\n---\n\n{next_result.message}"
                )
                return next_result

        return result

    # ── Branch 2: no open session — check for a learning log ────────────────
    learning_log = get_latest_learning_log(user_id, db_session)
    if learning_log is not None:
        # user_profile will be fetched and passed once the router is wired in
        # a later phase; pass an empty dict for now.
        topic = await generate_topic({}, learning_log)
        new_session: TutorSession = create_session(user_id, topic, db_session)
        result = await listening.run(message, new_session, db_session)
        result.message = f"{_NEW_SESSION_GREETING}\n\n{result.message}"
        return result

    # ── Branch 3: first ever visit — create onboarding session, run agent ────
    onboarding_session: TutorSession = create_session(user_id, "onboarding", db_session)
    return await onboarding.run(message, onboarding_session, db_session)
