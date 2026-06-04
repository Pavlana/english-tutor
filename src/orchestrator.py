"""Orchestrator — session manager and router.

Implements the deterministic decision table that decides which agent handles
each incoming message. This is a pure function of (message, DB state); it
holds no in-memory state between HTTP requests.
"""

import json

from sqlmodel import Session as DBSession

from src.db.repo import (
    create_session,
    get_latest_learning_log,
    get_open_session,
)
from src.models import AgentResult

# TODO(task-2.3): replace with real import once src/topic_generator.py exists
# from src.topic_generator import generate_topic

# TODO(task-2.x): replace with real imports once src/agents/ exists
# from src.agents.onboarding import run as run_onboarding
# from src.agents.listening import run as run_listening
# from src.agents.feedback import run as run_feedback

_TASK_ORDER: list[str] = ["listening", "writing", "speaking", "grammar"]
_RESUMABLE_STATUSES: frozenset[str] = frozenset({"not_started", "in_progress"})
_TERMINAL_STATUSES: frozenset[str] = frozenset({"complete", "skipped"})


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
            # All tasks complete or skipped — hand off to feedback agent.
            # TODO(task-2.x): dispatch to run_feedback(open_ts, db_session)
            return AgentResult(
                message="[feedback stub] All tasks done — feedback coming soon.",
                agent="feedback",
                task_status="not_started",
                usage=None,
            )

        # TODO(task-2.x): dispatch to the agent for next_task_name
        return AgentResult(
            message=f"[{next_task_name} stub] Resuming your session.",
            agent=next_task_name,
            task_status="in_progress",
            usage=None,
        )

    # ── Branch 2: no open session — check for a learning log ────────────────
    learning_log = get_latest_learning_log(user_id, db_session)
    if learning_log is not None:
        # TODO(task-2.3): replace stub with real call:
        #   topic = await generate_topic(user_profile, learning_log)
        topic = "general english"  # stub until task 2.3

        create_session(user_id, topic, db_session)

        # TODO(task-2.x): dispatch to run_listening(new_session, db_session)
        return AgentResult(
            message=f"[listening stub] Starting new session on '{topic}'.",
            agent="listening",
            task_status="not_started",
            usage=None,
        )

    # ── Branch 3: first ever visit — onboarding ──────────────────────────────
    # TODO(task-2.x): dispatch to run_onboarding(user_id, message, db_session)
    return AgentResult(
        message="[onboarding stub] Welcome! Let's get started.",
        agent="onboarding",
        task_status="not_started",
        usage=None,
    )
