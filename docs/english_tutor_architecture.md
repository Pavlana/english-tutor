# English Tutor — Architecture Document
**Version:** 0.1 | **Date:** May 2026 | **Status:** Design agreed, pre-implementation

---

## 1. System Overview

A multi-agent English tutor that runs structured learning sessions across four modalities: Listening, Writing, Speaking, and Grammar. An Orchestrator manages session state, routes to agents, and triggers the Feedback agent once all tasks are complete.

---

## 2. Agents

### Orchestrator
**Role:** Session manager and router. First to run on every user message.

**Input:** User message, user_id  
**Output:** Routes to appropriate agent or flow  
**Memory reads:** Session DB, Learning log DB, User profile DB  
**Memory writes:** Session state updates throughout

**Decision logic:**
```
On any session-opening message ("hi", "I'm here", etc.):
  1. DB read → latest learning log for user
  
  If learning log found AND status = complete:
      → Generate topic (LLM call using user profile + last learning log)
      → Create new session record
      → Start Listening agent
  
  If no learning log found:
      → DB read → any open session?
      
      If open session found:
          → Resume: surface completed tasks + start next incomplete task
      
      If no open session AND no learning log:
          → First ever session: start Onboarding flow
```

---

### Onboarding Agent (runs once, ever)
**Role:** Establish user's CEFR level, grammar baseline, interests. Appears as friendly conversation — evaluation is invisible to user.

**Flow:**
1. Conversation agent: 4–6 exchange open chat ("tell me about yourself, what brings you here")
2. Silent evaluator: takes full transcript, outputs structured user profile
3. Writes user profile to DB
4. Generates first learning log from profile
5. Hands off to Orchestrator to start first session

**Evaluator output (user profile):**
```json
{
  "user_id": "uuid",
  "cefr_level": "B1",
  "vocabulary_range": "adequate, some repetition",
  "grammar_gaps": ["passive voice", "reported speech"],
  "grammar_strengths": ["present perfect", "basic conditionals"],
  "confidence_level": "medium",
  "interests": ["technology", "travel"],
  "onboarding_transcript": "..."
}
```

**Note:** Onboarding conversation agent is NOT the Speaking agent. Different prompt, different goal, different output. Runs once only.

---

### Listening Agent
**Role:** Content acquisition and comprehension evaluation.

**Input:** Topic string, session_id  
**Output:** `{video_url, vocabulary_list, comprehension_summary}` → Orchestrator  
**Tools:** YouTube search, DB write  
**Memory writes:** New vocabulary (status: new), session.listening = complete

**Flow:**
```
1. Tool: YouTube search for topic → store URL
   If no video found → set session.listening = skipped, return topic string only

2. LLM: extract vocabulary from video transcript
3. LLM: generate comprehension questions (3–5 questions)
4. → Send to user: video URL + questions
5. User watches video, returns with answers (max 3 turns)
6. LLM: evaluate answers → comprehension score
7. LLM: generate listening summary
8. Tool: DB write → vocabulary list (status: new), session.listening = complete
9. → Return {summary, vocabulary_list} to Orchestrator
```

---

### Writing Agent
**Role:** Generate writing tasks, evaluate responses, assess vocabulary usage.

**Input:** `{topic, listening_summary or topic-only, vocabulary_list, learning_log_recommendations}` from Orchestrator  
**Output:** Task summary with vocabulary usage signals → Orchestrator  
**Tools:** Dictionary lookup API, DB write  
**Memory reads:** Learning log (recommendations block)

**Flow:**
```
1. LLM: generate writing task (short essay based on topic + vocabulary)
2. → Send task to user
3. User submits response
4. LLM: evaluate response
   - If acceptable → generate summary + "ready to move on?"
   - If needs refinement → specific feedback + ask for revision
5. Repeat up to max 3 turns
6. If summary generated OR max turns reached → task complete
7. LLM: vocabulary usage assessment per word (used correctly / not used / used incorrectly)
8. LLM: generate writing task summary
9. Tool: DB write → session.writing = complete, summary stored, vocabulary signals stored
10. → Return summary to Orchestrator
```

---

### Speaking Agent
**Role:** Generate speaking/discussion tasks, evaluate responses, assess vocabulary usage.

**Input:** `{topic, listening_summary or topic-only, vocabulary_list, learning_log_recommendations}` from Orchestrator  
**Output:** Task summary with vocabulary usage signals → Orchestrator  
**Tools:** Dictionary lookup API, DB write  
**Memory reads:** Learning log (recommendations block)

**Flow:** Same pattern as Writing agent. Discussion questions instead of essay task.

---

### Grammar Agent
**Role:** Generate grammar exercises targeted at identified gaps.

**Input:** `{grammar_gaps from learning log (last 3 sessions)}` from Orchestrator  
**Output:** Grammar task summary → Orchestrator  
**Tools:** Dictionary lookup API, DB write  
**Memory reads:** Learning log (grammar block), grammar history (last 3 sessions from DB)

**Flow:**
```
1. LLM: analyse grammar gaps from last 3 sessions
2. LLM: generate targeted grammar exercises
3. → Send exercises to user
4. User submits response
5. LLM: evaluate → correct / needs revision (max 3 turns)
6. LLM: generate grammar summary
7. Tool: DB write → session.grammar = complete, summary stored
8. → Return summary to Orchestrator
```

---

### Feedback Agent
**Role:** Synthesise session summaries, update vocabulary statuses, generate learning log for next session.

**Trigger:** Only runs when all four task statuses = complete (or skipped)

**Input:** All task summaries from DB, vocabulary usage signals from DB  
**Output:** Updated vocabulary statuses, new learning log  
**Tools:** DB read (all summaries, vocabulary signals), DB write (vocabulary statuses, learning log)

**Flow:**
```
1. Tool: DB read → all task summaries for session
2. Tool: DB read → vocabulary usage signals from Writing + Speaking agents
3. LLM: update vocabulary status per word
   (used correctly → learning++, not used → stays, mastered threshold → mastered)
4. Tool: DB read → grammar summaries last 3 sessions
5. LLM: identify grammar gaps (3-session window)
6. LLM: generate learning log for next session
7. Tool: DB write → updated vocabulary statuses, new learning log
8. Tool: DB write → session.status = complete
```

---

## 3. Tools (deterministic, no LLM reasoning)

| Tool | Used by | Purpose |
|------|---------|---------|
| YouTube search | Listening agent | Find video for topic |
| Dictionary lookup API | Writing, Speaking, Grammar agents | Word definitions during tasks |
| DB read | Orchestrator, all agents | Read session, learning log, vocabulary, user profile |
| DB write | All agents, Feedback agent | Write summaries, statuses, learning log |

**Not tools — these are LLM calls inside agents:**
- Vocabulary extraction (inside Listening agent)
- Summary generation (inside each agent)
- Comprehension question generation (inside Listening agent)
- Vocabulary usage assessment (inside Writing/Speaking agents)

---

## 4. RAG

**Current:** Not implemented. All retrieval is structured DB lookup by user_id, session_id, status field.

**Trigger for RAG introduction:** Vocabulary store reaches 500+ words, OR a new use case emerges where semantic similarity retrieval is needed (e.g. "find vocabulary words related to this topic that need review").

**When implemented:** Vector search over vocabulary corpus. Embeddings: VoyageAI. Local: Chroma. Deployed: Supabase pgvector.

---

## 5. Memory Model

| Type | What | Where |
|------|------|-------|
| Short-term | Current session context, user responses within a task turn | In-memory (passed between agent calls in same session) |
| Long-term | Learning log, vocabulary store, grammar history, session summaries | Persistent DB |
| Session persistence | Incomplete session state (summaries per task, task statuses) | Session DB record |

---

## 6. Session Completion Logic

**Task-level completion:**
- Summary generated by agent AND (user confirms "next" / "move on" OR max 3 turns reached)
- User explicitly saying "skip" → task status = complete with partial summary
- Abandonment (session closed mid-task) → task status = in_progress, partial state saved

**Session-level completion:**
- All tasks status = complete or skipped
- Triggers Feedback agent
- Learning log generated → session.status = complete

---

## 7. Session Order

Fixed order: **Listening → Writing → Speaking → Grammar**

Listening skipped if no video found → Writing receives topic string only, agents generate from internal knowledge.

User-selectable order: deferred to later version.

---

## 8. Topic Generation

**First session:** Derived from onboarding evaluation (user profile).

**Subsequent sessions:**
```
LLM prompt: Given user profile (CEFR level, interests), last session topic, 
and learning log, choose the next topic for logical progression.
```

No hardcoded topic list in current iteration. LLM chooses based on profile and progression logic.

---

## 9. Database Schemas

### Session
```json
{
  "session_id": "uuid",
  "user_id": "uuid",
  "topic": "climate change",
  "date_started": "2026-05-05T10:00:00Z",
  "status": "in_progress",
  "tasks": {
    "listening": {
      "status": "complete",
      "video_url": "https://...",
      "vocabulary": ["ubiquitous", "mitigate"],
      "summary": "..."
    },
    "writing": {
      "status": "in_progress",
      "task_generated": "Write a short essay...",
      "user_response": "...",
      "summary": null
    },
    "speaking": {
      "status": "not_started",
      "summary": null
    },
    "grammar": {
      "status": "not_started",
      "summary": null
    }
  }
}
```

**Task status values:** `not_started` | `in_progress` | `complete` | `skipped`

---

### Vocabulary Store
```json
{
  "word": "ubiquitous",
  "user_id": "uuid",
  "status": "learning",
  "times_seen": 4,
  "times_used_correctly": 1,
  "last_seen_session": "session_uuid",
  "topic_tags": ["technology", "climate"]
}
```

**Status values:** `new` | `learning` | `mastered`

**Note:** "word" can be an expression, phrasal verb, or collocation (e.g. "look forward to").

---

### Learning Log
```json
{
  "log_id": "uuid",
  "user_id": "uuid",
  "generated_after_session": "session_uuid",
  "date": "2026-05-05",
  "status": "active",
  "vocabulary_to_review": ["ubiquitous", "mitigate"],
  "grammar_focus": ["passive voice", "reported speech"],
  "grammar_gap_summary": "Consistent errors with passive construction over last 3 sessions.",
  "session_notes": "Strong vocabulary usage in writing. Speaking confidence improving.",
  "recommended_topic_tags": ["environment", "technology"]
}
```

---

### User Profile
```json
{
  "user_id": "uuid",
  "cefr_level": "B1",
  "vocabulary_range": "adequate, some repetition",
  "grammar_gaps": ["passive voice", "reported speech"],
  "grammar_strengths": ["present perfect", "basic conditionals"],
  "confidence_level": "medium",
  "interests": ["technology", "travel"],
  "onboarding_transcript": "..."
}
```

---

## 10. Full User Flow

### First ever session
```
User: "hi"
  → Orchestrator: DB read → no learning log, no open session
  → Start Onboarding flow
  → Onboarding agent: 4–6 turn friendly conversation
  → Silent evaluator: generates user profile
  → DB write: user profile
  → LLM: generate first learning log from profile
  → DB write: learning log
  → Orchestrator: generate topic
  → DB write: new session record
  → Listening agent starts
```

### Returning session (new)
```
User: "hi"
  → Orchestrator: DB read → learning log found, status = complete
  → LLM: generate topic from user profile + learning log
  → DB write: new session record
  → Listening agent starts
```

### Returning session (resuming incomplete)
```
User: "hi"
  → Orchestrator: DB read → no complete learning log
  → DB read → open session found
  → Surface to user: "You were working on [topic]. Writing and Speaking are done. Grammar is next."
  → Resume Grammar agent
```

### Within a task (all agents)
```
Agent generates task → sends to user
User submits response
Agent evaluates:
  → Acceptable: generates summary + "ready to move on?"
  → Needs work: specific feedback + request revision
User: "next" / "move on" / "skip" OR max 3 turns reached
  → Agent writes summary to DB, sets task status = complete
  → Returns summary to Orchestrator
Orchestrator routes to next task
```

### Session completion
```
All four tasks = complete or skipped
  → Orchestrator triggers Feedback agent
  → Feedback agent: reads all summaries + vocabulary signals
  → Updates vocabulary statuses
  → Generates grammar gap summary (3-session window)
  → Generates learning log for next session
  → Writes learning log to DB
  → Sets session.status = complete
  → Sends session summary to user
```

---

## 11. What Is Deferred

- Reading agent (article-based comprehension)
- User-selectable session order
- Topic selection presented to user
- Word quiz as alternative to vocabulary rotation
- RAG over vocabulary corpus (trigger: 500+ words)
- Article search tool (no home until Reading agent exists)

---

*Last updated: May 2026. Update after each implementation sprint.*
