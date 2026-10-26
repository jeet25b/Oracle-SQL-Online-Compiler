# Compiler rules review — 19 October 2026

## Result

The new assistance layer is a useful starting point, but not complete old-compiler parity. Existing 13 Node checks pass. Additional direct probes reproduced the three edge cases below. This audit does not change compiler behavior or approved settings. No live Oracle or browser password-manager test was performed.

## Findings to fix first

| Priority | Rule | Verified gap | Recommended correction |
|---|---|---|---|
| P1 | Suggestions outside data | `INSERT INTO EMP VALUES (1) RETURNING EM` returns no completion. The VALUES suppression regex does not stop at its matching closing parenthesis. | Use tokenizer nesting depth to limit suppression to the actual value list and resume identifier completion afterward. |
| P1 | Automatic indentation | Enter after `/*` followed by a newline and `BEGIN` adds indentation, although BEGIN is inside a block comment. The rule scans only the current line. | Derive lexical state from the whole prefix; do not apply code-block indentation inside comments or literals. |
| P1 | Invisible-character diagnostic | `SELECT 'A[NBSP]B' FROM DUAL;` receives a replace-with-space warning, even though the character is data inside a literal. | Separate visual visibility from syntax validity. Keep the approved outlined box, but do not recommend changing literal data as if it were an SQL separator error. |
| P1 | Transformation safety | Existing Format and Convert case still have the previously reported literal/comment risks. New assistance did not replace these handlers. | Reuse a token stream for all transformations and preserve literals/quoted identifiers byte-for-byte. |
| P1 | Execution selection | The previously reported full-buffer Run statement behavior is separate from autocomplete and remains an execution concern. | Implement explicit selection/current-statement/full-script semantics before expanding automation. |

## Other gaps found by source inspection

- Common function completions (`COUNT`, `SUBSTR`, `NVL`, `TO_DATE`) are absent from the new keyword list. Add a separate function catalog, signatures and argument hints.
- Metadata extraction uses regexes on raw SQL. References inside strings/comments can be mistaken for real tables. Use tokens and resolve aliases, schema-qualified objects, CTEs and scope.
- Column completions combine referenced tables without alias ownership. `e.` should prefer the columns of alias e and preserve qualifiers when inserted.
- Completion candidate order puts keywords before columns and caps the list at 25. Rank valid local identifiers and exact/contextual matches first, then functions and keywords.
- Diagnostics return only the first hint and recognize a small fixed typo list. Provide severity, source range and multiple nonblocking hints. Oracle remains authoritative at execution.
- Enter accepts the first suggestion immediately. In SQL*Plus this competes with Enter-to-execute; in PL/SQL it competes with newline/indentation. Prefer Tab for acceptance, arrows for explicit selection, Escape for dismissal. Make any change to existing accepted key behavior reviewable.
- Asynchronous metadata completion should invalidate pending responses on cursor movement, Escape, click and editor replacement, not just new input or blur. Otherwise an earlier request can reopen a dismissed popup.
- IME composition should suppress suggestion replacement and diagnostics until composition finishes; checking only keydown is insufficient.
- Programmatic edits use setRangeText and synthetic input. Verify undo/redo grouping and teacher metrics so accepting a suggestion is not counted as manually typing its whole expansion.
- Completion popup geometry should also be tested with the on-screen keyboard, visual viewport offsets, page zoom, long lists and fullscreen. Current geometry regression checks use mocked measurements, not real browser layout.
- Font, indentation width, autocomplete, live checks and acceptance key should have clear settings. Preserve currently approved defaults unless the user approves a change.

## Login rule review

The shared script empties recognized login fields when opened, enables username autocomplete hints after three characters, and requests password autocomplete suppression on password focus. It does not own the native password-manager popup and cannot guarantee its exact timing or hide it in every browser.

Avoid adding more browser-autofill workarounds without actual browser tests: readonly toggling and repeated field clearing can interfere with accessibility, pasted credentials and password managers. Test keyboard-only input, paste, IME, short account names, account switching and reopened dialogs. Ensure changing a username never submits an old account's password accidentally. Do not store saved browser passwords in application storage to imitate the popup.

## Suggested additional rules, in implementation order

1. **One SQL tokenizer for all assistance.** Share quote/comment/nesting state across completion, indentation, formatting, case conversion, diagnostics and statement targeting. Include Oracle alternative quoting and quoted identifiers.
2. **One explicit execution contract.** Run selection if selected, otherwise the current statement; Run script executes the whole buffer. Include a running guard, bind entry and source-relative error mapping.
3. **Predictable completion.** Suggest keywords, functions and identifiers; never fetch row values for suggestions. Keep suggestions out of literal data and comments. Support Ctrl+Space explicitly and respect Escape.
4. **Structural indentation.** Configurable indent width; inherit whitespace; indent after BEGIN/THEN/LOOP; align END/ELSE/ELSIF/EXCEPTION with the matching block. Handle tabs, multiline expressions and paste as one undoable action.
5. **Advisory validation only while typing.** Do not automatically run SQL or compile stored objects. Distinguish local hints from Oracle-reported errors.
6. **Safer transformations.** Preserve data and comments; preview larger formatting changes and support one-step undo.
7. **Transaction clarity.** Always show autocommit state and active editor connection; provide explicit commit/rollback actions and an optional all-row UPDATE/DELETE warning. Preserve current approved autocommit preferences.
8. **Resilient workspace.** Per-user draft recovery, bounded caches, metadata refresh after DDL, clear truncation warnings and explicit error recovery. Do not leak source/results across account changes.
9. **Teaching compatibility.** Preserve lab context, teacher access revocation, sharing modes and interaction metrics through every migrated editor action.

## Tests to add before claiming parity

- VALUES lists followed by RETURNING, nested functions and INSERT SELECT; string and comment contents resembling table references.
- Alias/CTE/subquery scope; quoted identifiers; function signatures and schema-qualified names.
- Enter inside multiline comments, ordinary strings and q-quoted strings; nested BEGIN/END; ELSE/ELSIF/EXCEPTION alignment.
- Invisible spaces inside literals versus between SQL tokens; multiple diagnostics with accurate source ranges.
- Delayed metadata responses after Escape/caret movement; Tab/Enter/Shift+Enter/Ctrl+Enter; undo/redo and IME.
- Browser checks at multiple zoom levels, with scrolling/fullscreen and an on-screen keyboard.
- Login checks in the actual browser with saved credentials; native UI timing cannot be certified by Node mocks.

## Evidence

- `online-oracle-compiler/editor-assist.js`: scanner, completion context, indentation, diagnostics and UI adapter.
- `online-oracle-compiler/oracle-sql-ide.js`: metadata provider, editor integration, transformation and execution handlers.
- `online-oracle-compiler/login-autofill.js`: shared login lifecycle and autocomplete hints.
- `global-controls.js`: shared caret geometry, hidden-character overlays and anchored menus.
- `tests/editor-assist.test.cjs`, `tests/starter-popup.test.cjs`, `tests/login-autofill.test.cjs`: the 13 existing passing checks.
- `reports/compiler-comparison-2026-10-19.md`: broader old/new comparison and source references.
