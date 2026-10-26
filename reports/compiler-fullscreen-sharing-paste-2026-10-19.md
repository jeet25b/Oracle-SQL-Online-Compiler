# Fullscreen, sharing and paste activity update

- Fullscreen explicitly overrides the collapsed-navigator grid and gives the
  compiler panel the full available column. Source-level fix; visual verification
  in the affected browser is still needed.
- Students no longer see typed/pasted/edit metrics in history tables or exports;
  the student history API also omits them. Teacher history keeps the metrics.
- Allow next statement is removed from the selector and rejected by the API.
  Legacy statement-mode grants are revoked when sharing is accessed.
- Private revokes teacher reads and writes for BOTH SQL*Plus and PL/SQL and clears
  shared server snapshots. It does not erase the student's local code, executed
  SQL history, or separately recorded paste activity. A teacher view rechecks
  access every four seconds and clears its displayed editor when denied.
- Old and new compiler inputs record only user-triggered paste events: student,
  editor, timestamp and pasted text. No clipboard polling, other-tab reading or
  password-field recording occurs. A visible notice informs students.
- Teacher/admin Paste activity shows counts and up to the latest 200 events;
  the existing teacher student-scope rule is enforced. Text is capped at 65,536
  characters per event; truncation is flagged. Recording is capped at 500 events
  per student per hour. No past events can be reconstructed retroactively.
- A paste event does not prove copying from another tab. The source tab and the
  number of copy actions cannot be determined through ordinary browser paste
  events. User-triggered pastes may also originate in the same tab or another app.

## Old compiler SQL*Plus execution rule check

Source: root app.js, especially isSqlPlusInputComplete, extractFirstCliStatement,
runPastedCliBatch, runCli and the delegated keydown/paste handlers.

- Enter normally runs a complete command; incomplete input receives a newline.
- Ordinary SQL is considered complete when it ends in a semicolon.
- Single-line recognized client commands, /, and @ script invocations have
  separate completeness rules; recognition is not proof a command is supported.
- Anonymous BEGIN/DECLARE blocks are considered complete at END; by the old
  frontend; stored-program detection differs between its completeness and batch
  extraction routines. This is a compatibility inconsistency, not a rule copied
  into the new tokenizer.
- With a completion menu open, Enter may accept a suggestion before execution.
- Shift+Enter bypasses the ordinary Enter-to-run handler to insert a newline.
- Newline-separated batches can be extracted one statement at a time, preserving
  the remainder. A pasted-batch helper exists; paste-handler conditions decide
  when it is invoked, so helper existence alone does not imply every paste runs.
- A running guard prevents overlapping old SQL*Plus commands. Local client
  commands run before Oracle submission; SQL execution gathers binds and can ask
  for confirmation for UPDATE/DELETE without WHERE.

The old execution rules were inspected, not changed as part of this update.

## Validation

Four new Django tests passed for paste recording, teacher scope, content cap,
Private revocation for both editors, rejection of the retired grant and student
history metric omission. JavaScript syntax and Django system checks passed.
Migration 0025_compiler_paste_event was applied. No live cross-tab source detection
is claimed, and no real student paste content was used in automated tests.
