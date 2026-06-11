"""Interactive REPL for manual end-to-end session testing.

Run with:
    uv run python tools/repl.py [user_id]

user_id defaults to "repl-user" if not supplied as argv[1].

Each line you type is passed to orchestrator.handle(). The response message
and the name of the agent that handled it are printed after every turn.
Type "quit" or press Ctrl-C to exit cleanly.
"""

import asyncio
import sys
from pathlib import Path

# Ensure the project root is on sys.path when the script is run directly
# (e.g. `uv run python tools/repl.py`), where tools/ is not a package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.engine import create_db_and_tables, get_session  # noqa: E402
from src.db.repo import get_open_session
from src.orchestrator import handle


def _print_session_state(user_id: str) -> None:
    """Print a summary of the user's current session state to stdout.

    Args:
        user_id: The user whose session state to display.
    """
    import json

    with get_session() as db:
        ts = get_open_session(user_id, db)

    if ts is None:
        print("  No open session.")
        return

    print(f"  Session : {ts.session_id}")
    print(f"  Topic   : {ts.topic}")
    tasks = json.loads(ts.tasks)
    for name, data in tasks.items():
        status = data.get("status", "?")
        print(f"  {name:<12} {status}")


async def _repl(user_id: str) -> None:
    """Run the read-eval-print loop for user_id.

    On startup, sends a silent empty message to trigger the tutor's greeting
    so the user sees a prompt immediately rather than a blank cursor.
    After onboarding completes, automatically advances to the first real task
    without requiring an extra keystroke from the user.

    Args:
        user_id: The user_id passed to orchestrator.handle() on every turn.
    """
    print(f"\nEnglish Tutor REPL  —  user: {user_id}")
    print("Type 'quit' or press Ctrl-C to exit.\n")
    print("Current session state:")
    _print_session_state(user_id)
    print()

    # Auto-greet: fire an empty message so the tutor speaks first.
    with get_session() as db:
        result = await handle("", user_id, db)
    print(f"\n[{result.agent}] {result.message}\n")

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if message.lower() == "quit":
            print("Goodbye.")
            break

        with get_session() as db:
            result = await handle(message, user_id, db)

        print(f"\n[{result.agent}] {result.message}\n")

        # After onboarding closes, automatically start the first real task
        # so the user doesn't need to type an extra message to advance.
        if result.agent == "onboarding" and result.task_status == "complete":
            with get_session() as db:
                result = await handle("", user_id, db)
            print(f"\n[{result.agent}] {result.message}\n")


def main() -> None:
    """Entry point — parse argv and launch the async REPL."""
    user_id = sys.argv[1] if len(sys.argv) > 1 else "repl-user"
    create_db_and_tables()
    asyncio.run(_repl(user_id))


if __name__ == "__main__":
    main()
