"""Shared utilities and turn-loop scaffolding reused by every task agent.

All task agents (listening, writing, speaking, grammar) delegate their
generate → user turns → evaluate → complete cycle to run_turn_loop.
The turn counter lives in session.turn_count in the DB; no in-memory
turn variables are used.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlmodel import Session as DBSession

from src.db.repo import set_task_status, update_task
from src.db.schemas import TutorSession
from src.models import AgentResult

# Maximum number of user evaluation attempts before the loop forces completion.
_MAX_TURNS: int = 3


def extract_json(raw: str) -> str:
    """Extract the first JSON array or object from a model response string.

    Models sometimes prepend prose before the JSON payload. This strips
    everything outside the outermost ``[…]`` or ``{…}`` so ``json.loads``
    can parse it cleanly. Available to all agents via ``from src.agents.base``.

    Args:
        raw: Raw model output that should contain a JSON array or object.

    Returns:
        The extracted JSON substring, or the original string if no bracket
        pair is found (letting the caller's json.loads raise naturally).
    """
    match = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
    return match.group(0) if match else raw


async def run_turn_loop(
    generate_fn: Callable[[], Awaitable[str]],
    evaluate_fn: Callable[[str], Awaitable[dict[str, Any]]],
    session: TutorSession,
    task_name: str,
    message: str,
    db_session: DBSession,
) -> AgentResult:
    """Drive a generate → evaluate → complete turn loop for a task agent.

    The loop is stateless between HTTP requests — all state is read from
    and written to the DB on every call.

    Turn flow:
      turn_count == 0 → call generate_fn(), store output, advance to 1,
                         return task_status="in_progress".
      0 < turn_count < _MAX_TURNS → call evaluate_fn(message):
        - acceptable  → persist summary, mark task "complete", reset turn_count.
        - not yet     → increment turn_count, return feedback, "in_progress".
      turn_count == _MAX_TURNS → call evaluate_fn one final time, accept the
        result regardless of the acceptable flag, mark "complete".

    On completion, session.turn_count is reset to 0 so the next task's
    loop starts fresh.

    Args:
        generate_fn: Async callable that produces the first task output
            (e.g. comprehension questions). Called with no arguments; returns
            the text to send to the user.
        evaluate_fn: Async callable that evaluates the user's response.
            Receives the raw user message string; returns a dict with keys:
            ``acceptable`` (bool), ``feedback`` (str), ``summary`` (str | None).
        session: The active TutorSession row — provides turn_count and session_id.
        task_name: The task key (e.g. ``"listening"``); used for DB updates.
        message: Raw text sent by the user for this turn.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="in_progress" while the loop continues,
        or task_status="complete" when the task is finished.
    """
    if session.turn_count == 0:
        generated = await generate_fn()
        update_task(session.session_id, task_name, {"generated": generated}, db_session)
        session.turn_count = 1
        db_session.add(session)
        db_session.commit()
        return AgentResult(
            message=generated,
            agent=task_name,
            task_status="in_progress",
            usage=None,
        )

    # Evaluate the user's response.
    eval_result = await evaluate_fn(message)
    force_complete = session.turn_count >= _MAX_TURNS

    if eval_result.get("acceptable") or force_complete:
        summary = eval_result.get("summary") or ""
        update_task(session.session_id, task_name, {"summary": summary}, db_session)
        set_task_status(session.session_id, task_name, "complete", db_session)
        # Reset turn_count so the next task's loop starts at 0.
        session.turn_count = 0
        db_session.add(session)
        db_session.commit()
        return AgentResult(
            message=eval_result.get("feedback", ""),
            agent=task_name,
            task_status="complete",
            usage=None,
        )

    session.turn_count += 1
    db_session.add(session)
    db_session.commit()
    return AgentResult(
        message=eval_result.get("feedback", ""),
        agent=task_name,
        task_status="in_progress",
        usage=None,
    )
