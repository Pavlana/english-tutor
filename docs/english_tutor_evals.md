# English Tutor — Eval Targets
**Version:** 0.1 | **Date:** May 2026 | **Status:** Agreed, pre-implementation

---

## Overview

Three layers of evals. Use in this order:

1. **Structural** — does the output have the right shape? Implemented as pytest unit tests. Run on every code change. Fast, deterministic, zero LLM cost.
2. **Behavioural** — does the output do the right thing? Either code-based (rule can be expressed as a condition) or LLM-as-judge (requires semantic understanding).
3. **Regression** — does a known good input still produce a known good output? Run after any prompt change. Deferred until prompts are stable.

Start with structural only. Add behavioural incrementally per agent once structural passes.

---

## Eval Targets by Agent

### Listening Agent

**Structural**
- Video URL is present and stored in DB
- Video summary is non-null
- Exactly 5 vocabulary items stored in DB with status `new`
- Between 3 and 5 comprehension questions generated
- User input summary is non-null after user responds
- Session task status written as `complete` or `skipped`, never left as `in_progress` after agent exits

**Behavioural**
- Code: each vocabulary word appears in the video transcript (string match against transcript)
- LLM-as-judge: comprehension questions are answerable from the video content, not from general knowledge

---

### Writing Agent

**Structural**
- Essay question or discussion prompt is non-null
- User input summary is non-null after user responds
- Session task status written as `complete`
- Vocabulary usage signals written to DB (one entry per vocabulary word)

**Behavioural**
- Code: essay question contains at least 2 words from the session vocabulary list
- LLM-as-judge: feedback on user response is specific to what they wrote, not generic boilerplate

---

### Speaking Agent

**Structural**
- Exactly 3 discussion questions generated
- Each question exceeds minimum length (not trivially short, e.g. > 10 words)
- User input summary is non-null after user responds
- Session task status written as `complete`

**Behavioural**
- LLM-as-judge: questions are open-ended (not yes/no), on-topic, and distinct from each other

---

### Grammar Agent

**Structural**
- Grammar exercises are non-null
- At least one exercise generated per identified grammar gap
- User input summary is non-null after user responds
- Session task status written as `complete`

**Behavioural**
- LLM-as-judge: exercises directly target the grammar gap identified in the learning log, not random or generic grammar

---

### Feedback Agent

**Structural**
- Learning log is generated only when all task statuses = `complete` or `skipped` — never before
- Vocabulary statuses updated only for words that appeared in the current session — no other words modified
- Learning log contains: at least one grammar gap, vocabulary to review, next session topic
- Session status written as `complete`

**Behavioural**
- Code: next session topic is different from the current session topic
- LLM-as-judge: next session topic is topically related to the current topic (logical progression, not random)
- LLM-as-judge: grammar gap summary references patterns from the last 3 sessions, not just the current one

---

## Implementation Notes

### Structural evals are pytest unit tests

```python
def test_listening_agent_returns_vocabulary():
    result = listening_agent(mock_context)
    assert result.vocabulary is not None
    assert len(result.vocabulary) == 5
    assert all(w.status == "new" for w in result.vocabulary)

def test_listening_agent_writes_status():
    result = listening_agent(mock_context)
    session = db.get_session(mock_context.session_id)
    assert session.tasks["listening"]["status"] in ["complete", "skipped"]
```

### Code-based behavioural evals

```python
def eval_vocabulary_coverage(task_generated: str, vocabulary_list: list) -> float:
    used = [w for w in vocabulary_list if w.lower() in task_generated.lower()]
    return len(used) / len(vocabulary_list)

# Writing agent: pass if >= 2 vocabulary words appear in the task
assert eval_vocabulary_coverage(result.task, session.vocabulary) >= 0.4
```

### LLM-as-judge pattern

```python
judge_prompt = """
You are evaluating an English tutor grammar exercise.
Identified grammar gap: {grammar_gap}
Exercise generated: {exercise}

Does this exercise directly target the identified grammar gap?
Answer: YES or NO. Then one sentence explaining why.
"""

response = anthropic.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=100,
    messages=[{"role": "user", "content": judge_prompt}]
)
```

Use LLM-as-judge only where the quality criterion requires semantic understanding. Use code-based checks everywhere you can express the rule as a condition.

---

## Build Order

| Phase | What to add |
|---|---|
| Sprint 1 (with each agent) | Structural evals — written alongside agent code, not after |
| Sprint 2 (Writing + Grammar working) | Code-based behavioural: vocabulary coverage, question length |
| Sprint 3 (all agents passing structural) | LLM-as-judge: Grammar exercises, Speaking questions, Feedback topic |
| After prompts stabilise | Regression suite |

---

## What "Done" Means per Agent

An agent is considered eval-complete when:
1. All structural evals pass in CI
2. At least one behavioural eval is implemented and passing
3. Eval results are logged (input, output, pass/fail, timestamp) to a JSON file or DB table

Logging eval results is the minimum observability requirement. Without it you cannot detect regressions after prompt changes.

---

*Last updated: May 2026. Update when new agents are added or eval criteria change.*
