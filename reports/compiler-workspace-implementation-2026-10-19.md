# New compiler workspace implementation — 19 October 2026

Implemented in the new `/oracle-sql/` workspace. Approved toolbar layout,
autocommit preferences, popup placement and existing teacher/student features
are retained.

## Delivered

- Shared frontend tokenizer: existing `editor-assist.scan` now drives source
  highlighting, completion contexts, indentation, diagnostics, formatting,
  case conversion, bind discovery and current-statement boundaries. Server-side
  Python lexical handling independently protects execution boundaries and binds.
- Run statement: uses nonempty selection, otherwise the statement containing the
  caret. Run script uses the full buffer. Explicit statement requests containing
  multiple statements are rejected server-side. Anonymous/stored PL/SQL blocks
  remain whole; use a standalone slash between blocks and subsequent statements.
- Safe transformations: quoted strings, alternative q quotes, quoted identifiers
  and comment contents are preserved. A format selection cutting through one of
  those protected tokens is rejected rather than rewriting part of its data.
- Bind dialog: unique names, case-insensitive matching, text/number/NULL inputs.
  Cancel or Escape ends the operation before execution, teacher snapshot or
  execution-history submission. Bind values are not persisted as drafts.
- Undo/redo: each formatting, completion or indentation action can be undone with
  Ctrl/Cmd+Z, redone with Ctrl/Cmd+Shift+Z or Ctrl+Y. New edits invalidate redo.
  The history is bounded to 100 revisions per input for the current page session.
- Draft recovery: separate local drafts for signed-in user, workspace route and
  editor ID; restored only into an empty editor. A recovery notice offers Discard.
  Saves are debounced and flushed on page hide; blocked/full storage is reported.
  Existing source is never overwritten by an old recovered draft. Drafts remain
  on this browser until discarded or cleared and are not cross-device storage.
- Related correctness: suppress indentation rules inside multiline comments,
  resume completions after completed VALUES lists, avoid treating invisible data
  inside literals as an invalid SQL separator, and preserve PL/SQL's final
  semicolon during execution preparation.

## Validation

28 Node tests cover lexical behavior, targeting, protected transformations,
bind dialog cancellation and type validation, undo/redo, recovery isolation,
existing popup geometry and login behavior. 16 Django tests cover backend
lexical boundaries, bind extraction, PL/SQL terminators, rejection of batches in
statement mode and the existing password-reset flow. All passed.

Tests use mocked DOM/Oracle boundaries; no live Oracle execution or full browser
layout/IME certification was performed. Local syntax hints remain advisory and
never automatically execute or compile database code. Bind input currently
covers input values, not a full Oracle OUT-bind/type editor.

## Files

- `online-oracle-compiler/editor-assist.js`: shared tokenizer and assistance.
- `online-oracle-compiler/workspace-core.js`: targeting, transforms, binds,
  history and recovery.
- `online-oracle-compiler/oracle-sql-ide.js`: UI integration and execution.
- `workbench/sql_lexer.py`, `workbench/views.py`: backend execution preparation.
- `tests/workspace-*.test.cjs`, `tests/bind-dialog.test.cjs`,
  `workbench/tests_sql_lexer.py`: added regression coverage.

Restart the Django server and refresh the compiler to load the updated assets.
