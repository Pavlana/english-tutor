"""Topic generation — selects the next session topic using an Opus call."""

import json
from pathlib import Path

from src.api_client import call_anthropic

_PROMPT_PATH = Path(__file__).parent / "prompts" / "topic_generation.txt"


async def generate_topic(user_profile: dict, learning_log: dict) -> str:
    """Generate a short topic noun phrase for the next tutoring session.

    Calls the Opus model with the learner's profile and most recent learning
    log. The model returns a short noun phrase (e.g. "climate change") that
    fits the learner's interests and CEFR level and does not repeat the
    previous session's topic.

    Args:
        user_profile: Dict matching the UserProfile shape from architecture §9.
        learning_log: Dict matching the LearningLog shape from architecture §9,
            representing the learner's most recent session summary.

    Returns:
        A short topic noun phrase chosen by the model.
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    context = json.dumps(
        {
            "user_profile": user_profile,
            "last_session_summary": learning_log,
        },
        indent=2,
    )

    topic, _ = await call_anthropic(
        messages=[{"role": "user", "content": context}],
        tier="opus",
        agent="topic_generator",
        system=system_prompt,
    )
    return topic.strip()
