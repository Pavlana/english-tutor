"""Orchestrator — session manager and router.

Implements the deterministic decision table that decides which agent handles
each incoming message. This is a pure function of (message, DB state); it
holds no in-memory state between HTTP requests.
"""

import json
from collections.abc import Callable

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
_RESUMABLE_STATUSES: frozenset[str] = frozenset({"not_started", "in_progress"})

_TASK_AGENTS: dict[str, Callable] = {
    "listening": listening.run,
    "writing": writing.run,
    "speaking": speaking.run,
    "grammar": grammar.run,
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
        tasks = json.loads(open_ts.tasks)
        next_task_name = _next_task(tasks)

        if next_task_name is None:
            return await feedback.run(message, open_ts, db_session)

        return await _TASK_AGENTS[next_task_name](message, open_ts, db_session)

    # ── Branch 2: no open session — check for a learning log ────────────────
    learning_log = get_latest_learning_log(user_id, db_session)
    if learning_log is not None:
        # user_profile will be fetched and passed once the router is wired in
        # a later phase; pass an empty dict for now.
        topic = await generate_topic({}, learning_log)
        new_session: TutorSession = create_session(user_id, topic, db_session)
        return await listening.run(message, new_session, db_session)

    # ── Branch 3: first ever visit — onboarding ──────────────────────────────
    return await onboarding.run(message, user_id, db_session)
