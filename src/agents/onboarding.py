"""Onboarding agent stub.

Runs once per user to establish their CEFR level, grammar baseline, and
interests. Onboarding has no corresponding task entry in session.tasks.
"""

from sqlmodel import Session as DBSession

from src.models import AgentResult


async def run(message: str, user_id: str, db_session: DBSession) -> AgentResult:
    """Stub: greet the user and signal that onboarding has started.

    No task status is set here — onboarding operates outside the standard
    session task lifecycle.

    Args:
        message: Raw text sent by the user.
        user_id: Identifies the user being onboarded.
        db_session: Active database session.

    Returns:
        AgentResult with agent="onboarding" and task_status="complete".
    """
    return AgentResult(
        message="Welcome! I'm your English tutor. Tell me a bit about yourself.",
        agent="onboarding",
        task_status="complete",
        usage=None,
    )
