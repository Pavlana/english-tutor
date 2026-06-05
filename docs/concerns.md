# Known Concerns & Technical Debt

Issues noticed during implementation that are not blocking MVP but should be
revisited before production.

---

## Onboarding

### No session expiry
If a user abandons onboarding mid-flow and returns later, the orchestrator
resumes from exactly where they left off (correct by design — all state is in
the DB). However, there is no timeout or expiry mechanism. A user who quit
mid-conversation a week ago will be dropped back into turn 3 of a stale
conversation. For MVP (single user, local) this is fine. At scale, a
configurable expiry on `TutorSession` rows with `topic="onboarding"` would
reset stale sessions.

### Partial write on crash is not idempotent
If `_novice_path()` or `_experienced_path()` crashes between writes (e.g.
`write_user_profile()` succeeds but `_generate_first_log()` throws), the
onboarding task is left at `"in_progress"` with no clean recovery path. On
retry, `write_user_profile()` will upsert safely, but `_generate_first_log()`
could create a duplicate log. A transaction wrapping both writes, or an
idempotency check before writing the log, would close this gap.

### `_evaluate_transcript()` stub must close the session (task 3.4)
Task 3.4 is not yet implemented. Until it lands, experienced-path users who
complete all conversation turns will have `tasks["onboarding"]["status"]`
stuck at `"in_progress"` and `TutorSession.status` still `"in_progress"`.
When task 3.4 is implemented, `_evaluate_transcript()` must:
1. Call `set_task_status("onboarding", "complete")`.
2. Set `session.status = "complete"` and commit — same pattern as `_novice_path()`.
Without step 2 the orchestrator's next call re-enters onboarding instead of
falling through to branch 2 and starting the first real session.
