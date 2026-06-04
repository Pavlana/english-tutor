"""Listening agent stub."""

from sqlmodel import Session as DBSession

from src.db.repo import set_task_status
from src.db.schemas import TutorSession
from src.models import AgentResult


async def run(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Stub: mark the listening task complete and return a placeholder result.

    Args:
        message: Raw text sent by the user.
        session: The active TutorSession row.
        db_session: Active database session.

    Returns:
        AgentResult with agent="listening" and task_status="complete".
    """
    set_task_status(session.session_id, "listening", "complete", db_session)
    return AgentResult(
        message="Listening task complete.",
        agent="listening",
        task_status="complete",
        usage=None,
    )
