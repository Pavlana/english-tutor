"""Feedback agent stub.

Runs after all session tasks are complete. Closes the session and will
eventually synthesise a learning log for the next session.
"""

from sqlmodel import Session as DBSession

from src.db.schemas import TutorSession
from src.models import AgentResult


async def run(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Stub: mark the TutorSession complete and return a placeholder result.

    Sets session.status = "complete" on the TutorSession row rather than
    setting an individual task status, since feedback operates at the session
    level.

    Args:
        message: Raw text sent by the user.
        session: The active TutorSession row to close.
        db_session: Active database session.

    Returns:
        AgentResult with agent="feedback" and task_status="complete".
    """
    session.status = "complete"
    db_session.add(session)
    db_session.commit()
    return AgentResult(
        message="Session complete. Great work today!",
        agent="feedback",
        task_status="complete",
        usage=None,
    )
