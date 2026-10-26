# SQL*Plus newline handling: old versus new

Compared current working files, not restore-point copies. Static source review; no live Oracle or browser execution certification. Old: root app.js. New: online-oracle-compiler/oracle-sql-ide.js and workspace-core.js. Both can call the current shared Django backend. Choices are proposals only and do not modify compiler settings. Previously approved project preferences remain in effect. For each point, select Old, New, or Custom; leave blank to defer. Recommendations are not selected automatically.

## 1. Enter on unfinished SQL

**Old:** Inserts a newline at the caret when the input is incomplete.
**New:** Inserts a newline at the caret when terminalInputIsComplete returns false.
**Recommendation:** Keep new
**Evidence:** app.js: isSqlPlusInputComplete and cliOutput keydown; oracle-sql-ide.js: terminalInputIsComplete and terminalInput keydown

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 2. Shift+Enter

**Old:** Leaves the native textarea newline behavior in place.
**New:** Explicitly inserts a newline; its handler does not dispatch an input event in this branch.
**Recommendation:** Keep new, add input event if layout needs it
**Evidence:** app.js: cliOutput keydown; oracle-sql-ide.js: terminalInput keydown

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 3. Pasted SQL: semicolon followed by newline

**Old:** extractFirstCliStatement extracts the first such statement; runPastedCliBatch awaits each request.
**New:** pastedBatch extracts commands at this boundary; runPastedTerminalBatch awaits each request.
**Recommendation:** Keep new
**Evidence:** app.js: extractFirstCliStatement/runPastedCliBatch; workspace-core.js: pastedBatch

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 4. Several statements on the same physical line

**Old:** The paste extractor does not split on semicolons without a following newline. A complete remainder can be submitted together.
**New:** The paste extractor also requires a following newline. A complete remainder can be submitted together.
**Recommendation:** Choose whether to add semicolon-only splitting as a separate future rule
**Evidence:** Same extractors; oracle-sql-ide.js: runPastedTerminalBatch

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 5. Final statement without a trailing newline

**Old:** Runs the remaining text when isSqlPlusInputComplete accepts it.
**New:** Runs the remaining text when terminalInputIsComplete accepts it.
**Recommendation:** Keep new
**Evidence:** app.js: runPastedCliBatch; oracle-sql-ide.js: runPastedTerminalBatch

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 6. Anonymous PL/SQL blocks

**Old:** Interactive BEGIN/DECLARE text is complete when it ends with END;. The paste extractor refuses to split a block beginning with BEGIN/DECLARE.
**New:** Requires a slash on its own line to complete a BEGIN/DECLARE block; lexical ranges keep internal semicolons together.
**Recommendation:** Keep new slash-line rule
**Evidence:** app.js: isSqlPlusInputComplete/extractFirstCliStatement; workspace-core.js: ranges; oracle-sql-ide.js: terminalInputIsComplete

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 7. Stored PL/SQL objects

**Old:** The paste extractor refuses to split CREATE PROCEDURE/FUNCTION/PACKAGE/TRIGGER/TYPE, but the general completeness fallback accepts a final semicolon.
**New:** Recognizes these as PL/SQL and waits for a separate slash line.
**Recommendation:** Keep new
**Evidence:** Same completeness/extraction functions

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 8. Semicolons inside strings and comments

**Old:** Extractor tracks ordinary single/double quotes and line/block comments. It does not implement Oracle q-quote delimiters.
**New:** Shared lexer handles protected tokens including Oracle q-quotes; these semicolons do not split statements.
**Recommendation:** Keep new
**Evidence:** app.js: extractFirstCliStatement; workspace-core.js: ranges; editor-assist.js: scan

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 9. Single-line comments and newline significance

**Old:** A -- comment ends at newline; leading comments can be echoed separately.
**New:** A -- comment ends at newline; leading comments are echoed separately. Removing the newline can hide later SQL in both compilers.
**Recommendation:** Keep new; never guess where a flattened comment ends
**Evidence:** app.js: splitLeadingCliComments; workspace-core.js: leadingComments; workbench/sql_lexer.py: tokens

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 10. Blank lines between comments

**Old:** Leading comments are separated, but this audit does not establish identical blank-line output in every case.
**New:** leadingComments emits blank prompt entries for extra blank lines between leading comments and SQL. Blank lines alone do not execute SQL.
**Recommendation:** Keep new
**Evidence:** app.js: splitLeadingCliComments; workspace-core.js: leadingComments

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 11. Unknown prose followed by a newline

**Old:** Special heuristics detach some prose/numbered headings only when later text begins with recognized SQL. Other prose can remain attached.
**New:** Unknown leading physical lines are queued separately and receive SP2-0734; following SQL is processed separately. The splitter has a finite command-name list.
**Recommendation:** Keep new
**Evidence:** app.js: extractFirstCliStatement; workspace-core.js: pastedBatch; workbench/views.py: execute_sql

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 12. Paste display and sequential echo

**Old:** Paste first enters the input; execution starts after two animation frames. Commands are echoed by runCli before work.
**New:** A detected batch is queued before painting it in the input. Each command is echoed before its execution request.
**Recommendation:** Keep new (already requested)
**Evidence:** app.js: keepCaretAfterPaste/runCli; oracle-sql-ide.js: paste handler/execute

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 13. Copied prompts and continuation line numbers

**Old:** runCli strips an initial SQL> prefix; the reviewed old paste handler does not remove a full continuation-number sequence.
**New:** stripSqlPlusPrompts removes recognized prefixes/sequences. Display line numbers use CSS labels outside SQL text. Recognition is heuristic, not arbitrary numeric deletion.
**Recommendation:** Keep new (already requested)
**Evidence:** app.js: runCli/keepCaretAfterPaste; workspace-core.js: stripSqlPlusPrompts; oracle-sql-ide.js: appendTerminalResult

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 14. Windows and Unicode newline normalization

**Old:** Paste changes CRLF/CR to LF. Clean code removes some invisible characters; no explicit U+2028/U+2029 conversion was found in that paste path.
**New:** Always changes CRLF/CR to LF. With Clean code enabled, U+2028/U+2029 also become LF.
**Recommendation:** Keep new
**Evidence:** app.js: keepCaretAfterPaste; workspace-core.js: clean; oracle-sql-ide.js: paste handler

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________

## 15. Newlines before Oracle execution

**Old:** Current shared Django backend uses the same prepare_statement path for both frontends when they call this endpoint.
**New:** Execution-copy whitespace cleanup collapses whitespace gaps and retains a newline when a gap contained a line break; quoted tokens/comments retain their contents. Editor text is not rewritten by this step.
**Recommendation:** Keep shared backend behavior (already requested)
**Evidence:** workbench/sql_lexer.py: normalize_execution_whitespace/prepare_statement

- [ ] Apply old rule to new SQL*Plus
- [ ] Keep current new rule
- [ ] Custom rule (write your choice below)

Custom rule: ____________________
