# Old and new Oracle compiler: option-by-option comparison

Date: 19 October 2026. Purpose: select useful old-compiler behavior for the new compiler without replacing its education features or changing approved preferences.

## Scope and confidence

**Old compiler** means the current root `index.html` and `app.js`, served by `compiler_workspace` at `/oraclesql/workspace/`. **New compiler** means `templates/oracle_sql_workspace.html` and `online-oracle-compiler/oracle-sql-ide.js`, served at `/oracle-sql/`. This is a comparison of the current working files, including recent changes, not a comparison against a dated restore point. The README describes some older defaults and routes; actual handlers take precedence here.

Both frontends use the current Django APIs in `workbench/views.py`. The retained `server.js` is a historical backend, not the backend selected by the normal Django startup path. Consequently, an old frontend feature is not automatically evidence of complete current backend support.

Method: inspect controls, their event handlers, persistence, request payloads, and backend handling. This is a static source audit, not a live Oracle execution benchmark or a browser compatibility certification. “Missing” below means no corresponding implementation was found in the reviewed new frontend/shared backend; native browser behavior is not counted as an application feature. Command names in a recognition regex or help string do not establish working support.

Priority: **P1** = correctness or major daily-use gap; **P2** = useful productivity improvement; **P3** = specialist or later enhancement. References such as O4/N3/B2 resolve to source links at the end.

## Main recommendation

Keep the new compiler as the destination. Transfer the old compiler's statement selection, bind-entry workflow, SQL-aware editing tools, result-table tools, and selected SQL*Plus emulation in separate modules. Preserve the new compiler's centralized teacher history, lab context, sharing permissions, interaction metrics, and current UI improvements.

Resolve execution-contract differences before migrating large feature sets. In particular, the new Run statement handler sends the entire buffer, and the backend does not branch on either frontend's statement/script mode flag.

## 1. Running SQL and PL/SQL

| Option | Old compiler | New compiler | Recommendation |
|---|---|---|---|
| Run current statement | `selectStatementRange()` selects a nonempty selection or finds a statement around the caret; PL/SQL has a whole-buffer special case. | Run statement calls `execute(editor.value, {script:false})`, passing the whole editor. | **P1:** port the selection workflow, with a stronger tokenizer. Acceptance: executing the middle statement in a three-statement buffer executes only that statement. O3/N3 |
| Execute selected SQL | Selected text is sent as a script from the old statement action. | Both toolbar and Ctrl+Enter pass the full buffer. | **P1:** define selection precedence explicitly and use it for toolbar and shortcut paths. O3/N3 |
| Run script | Old frontend sends `mode`; can split SQL*Plus commands into individual operations. | New frontend sends `script`; also preserves selection and scroll after execution. | **P1:** normalize the API contract. Backend currently splits and loops over the submitted SQL without consulting either mode flag. Do not assume button labels imply different server execution. O3/N2/N3/B2 |
| Named bind variables | Collects bind values and sends `binds`; allows cancelling before execution. | New execute payload does not include bind values or offer a collection dialog. | **P1:** reuse the bind workflow and existing backend `driver_binds`; skip colons in strings, comments, and PL/SQL assignment. O3/N2/B1 |
| UPDATE/DELETE without WHERE | `destructiveSqlWarning()` offers confirmation before sending SQL. | No equivalent pre-execution confirmation found in `execute()`. | **P1:** add an advisory confirmation with a reviewed SQL tokenizer. The old regex is a starting point, not a complete SQL analyzer. O3/N2 |
| Duplicate execution protection | Old run disables both editor Run buttons for the request. | Terminal disables its Run button, but editor run handlers lack an equivalent shared running guard. | **P1:** add one in-flight guard for the editor, covering mouse and keyboard. Preserve editing while running if desired. O3/N2/N3/N7 |
| Keep caret and scroll | Old toolbar helpers and execution selection logic preserve useful editing context. | New execute explicitly saves and restores selection and both scroll axes. | Keep the new restoration behavior while adding correct statement targeting. O3/N2 |
| Error location in source | Old code maps returned statement/offset details into diagnostics and terminal command markings. | New UI renders an error message but no matching source range. | **P1:** port source-offset mapping, including batch offsets and Oracle compilation line/column errors. O2/O3/N2/B2 |
| Mixed SQL and client commands | Old editor partitions recognized SQL*Plus client commands and SQL. | PL/SQL editor submits its buffer directly in non-SQL*Plus mode. | **P2:** either add documented mixed-script support or explicitly route client commands to SQL*Plus. O3/N2/B2 |
| Auto commit | Both editor and terminal controls exist and send state. | Both controls exist and persist state; terminal state can be updated from server response. | Retain approved preferences. Do not adopt README defaults without checking stored values and actual code. O3/O5/N2/N3 |
| Explicit COMMIT/ROLLBACK | SQL statements can be submitted; an old transaction helper references button IDs. | SQL statements can be submitted; no matching dedicated controls in current new template. | **P2:** optional dedicated controls, but verify rendered controls before copying the old helper. Do not report helper existence as proof of visible buttons. O3/N3/B2 |
| Separate editor/terminal transactions | Requests distinguish `sqlplus` mode. | Same distinction. | Preserve separate `editor` and `sqlplus` backend connections. Verify which connection each transaction affects. B2 |

## 2. Editor options

| Option | Old compiler | New compiler | Recommendation |
|---|---|---|---|
| Autocomplete while typing | Suggestions use SQL context, object metadata, aliases, and keyword lists. | Starter snippets exist; no comparable completion engine found. | **P1:** extract the old completion engine behind an editor adapter; test alias/table/column completion. O1/N3 |
| Starter popup | Old supports starter/zero-state suggestions with shared settings. | Separate PL/SQL and SQL*Plus starter menus with persisted toggles. | Keep new popup placement fixes; expand suggestions from the old engine without duplicating menus. O1/N3/N7/G1 |
| Syntax highlighting | Old token-oriented renderer includes special handling and editor context. | New renderer uses chained regular-expression replacements. | **P1:** migrate a single tokenization pipeline so generated markup is not processed as SQL. O2/N3 |
| Diagnostics while typing | Heuristic SQL validation and diagnostic highlighting exist. | No equivalent live diagnostic engine found. | **P2:** port as clearly advisory diagnostics. Oracle remains the authority; avoid blocking valid SQL based on heuristics. O2/N3 |
| Bracket/quote pairing and matching | Explicit pairing and matching handlers. | Basic textarea editing; Tab inserts two spaces. | **P2:** port pairing with selection wrapping, quote escaping, and undo tests. O2/O6/N3 |
| Find and replace | Custom find/replace, match counts, next match, replace one/all. | No equivalent application panel found. | **P1:** add a shared find/replace module for both inputs; keep it scoped to the active editor. O7/N3 |
| Editing shortcuts | Comment toggling, indentation, line movement/deletion, selection helpers, undo support, word-wrap shortcut. | Ctrl+Enter, Ctrl+Shift+Enter and basic Tab handling; terminal has its own keys. | **P2:** port shortcuts selectively and document conflicts with browser and accessibility keys. O6/N3/N7 |
| SQL formatting | Regex-based line breaks; can affect SQL literals/comments. | Regex collapses all whitespace before formatting, including whitespace inside literals/comments. | **P1:** replace both formatters with a token-aware formatter. Do not copy either as a finished solution. Example: `SELECT 'A  B' FROM DUAL;` must preserve the literal exactly. O8/N3 |
| Case conversion modes | Five modes, including keyword/identifier split; token handling protects comments, literals, quoted identifiers. | Three modes; supports selected range but directly transforms all text in that range, including literals. | **P1:** combine old token safety with new selection scope. Acceptance: converting `SELECT 'AbC' FROM "MixedTable"` must preserve both quoted values. O3/N3 |
| Font size | Per-editor controls and settings. | Per-editor size controls and persistence. | Keep existing new settings. O6/N3/N8 |
| Font family | Independent selectors for terminal and editor. | No equivalent selectors in new settings. | **P2:** port selectors and persist them independently; sync all mirrored text layers. O6/N8 |
| Hidden characters | Shared in-place overlay is loaded by the old page. | Same overlay is loaded by the new page. | Already shared: no migration needed. Regression-test overlay geometry after font, wrap, and scroll changes. G1 |
| Clean code | Paste-normalization controls. | Paste-normalization controls. | Already present; preserve approved labels and settings. Normalization can remove hidden characters before rendering. O7/N3/N7 |
| Vertical scrolling | Old editor manages highlight/textarea viewport synchronization. | New editor has explicit visible scrollbar styling and scroll synchronization. | Preserve the recent new scrollbar change. O2/N3/N9 |
| Line count and cursor indicator | Cursor indicator exists. | Live line count now follows cursor position in the second toolbar. | Preserve recent new layout. No need to restore the removed filename label. N0/N3 |

## 3. SQL*Plus command support

The old compiler implements many commands in JavaScript before calling Oracle. The new compiler largely relies on server handling. These are browser emulations, not a complete Oracle SQL*Plus executable.

| Option or command | Old compiler | New compiler / current backend | Recommendation |
|---|---|---|---|
| SQL and PL/SQL entry | Browser prompt creation, multiline handling and batch processing. | Multiline input, semicolon detection, slash-terminated PL/SQL and command history. | Preserve new input handling, but regression-test PL/SQL, comments and alternative quoting before changing the parser. O5/N7/B1 |
| DESC/DESCRIBE | Frontend helper and shared metadata APIs. | Backend explicitly implements describe handling. | Present in both. Test quoted and schema-qualified names before claiming complete identifier support. O3/O5/B2 |
| SET AUTOCOMMIT/SERVEROUTPUT/FEEDBACK/HEADING | Old browser settings are emulated locally. | Backend explicitly handles these six SET options, including LINESIZE and PAGESIZE. | **P1:** centralize settings ownership and test effects, not just acknowledgment messages. O5/B2 |
| SET LINESIZE/PAGESIZE | Old rendering has formatting settings. | Backend stores values; reviewed result path does not apply them to table width/pagination. | **P2:** define meaningful browser equivalents and implement their rendering effects. A successful SET message is not proof of output formatting. O5/N2/B2 |
| SHOW USER/ALL/settings/ERRORS | Explicit old command handlers. | Input recognizer accepts SHOW, but backend has no corresponding general SHOW handler. | **P1:** port common SHOW commands; use status/user_errors APIs where appropriate. O5/N7/B2 |
| HELP, PROMPT, REMARK, PAUSE | Old local help/output/pause handlers. | Several are recognized as complete input, but are not implemented by the reviewed backend. | **P1:** add local handlers before sending SQL; use application dialogs instead of copying blocking browser prompts wholesale. O5/N7/B2 |
| DEFINE/UNDEFINE/ACCEPT and substitution | Browser variable storage, expansion and interactive collection exist. | Recognizes some command names; no matching substitution engine in new flow. | **P1/P2:** port variables and `&` substitution as one coherent feature, with cancellation and quoting tests. O5/N7 |
| VARIABLE/PRINT | Old browser variable declarations and printed stored values. | No equivalent workflow found. | **P3:** inspect actual bind execution/OUT-bind support before advertising full VARIABLE compatibility; old local storage alone does not establish that. O5/B1 |
| LIST/RUN/GET/EDIT/APPEND/CHANGE/DEL | Old SQL buffer operations and editor handoff. | Some recognized as complete commands, but no corresponding server handlers. | **P2:** introduce an explicit SQL buffer object, then port buffer operations with tests. O5/N7/B2 |
| START, @, @@ and saved scripts | Old file retrieval and script execution, with argument parsing. | Recognizer accepts START/@ forms; current execute backend does not load these files. | **P2:** reuse safe `/api/files` integration; define nested-script depth and failure behavior. O5/B4 |
| SPOOL | Old browser transcript collection and download. | New reads `sqlPlusSpool` response fields, but the reviewed backend does not emit them. | **P2:** implement end-to-end spool behavior, preferably a browser transcript service. Current new response hooks are incomplete plumbing. O5/N2/B2 |
| CLEAR SCREEN | Old browser clears terminal output. | Toolbar Clear exists; execute has a `clearTerminal` response hook, but backend does not emit it. | **P2:** handle the typed command locally and reuse the toolbar clear operation. O5/N2/N7/B2 |
| COLUMN, BREAK, COMPUTE and titles | Old parsing and rendering state for report formatting. | No comparable working implementation found. | **P3:** port only if used in classes; treat as a tested compatibility subset, not full SQL*Plus parity. O5 |
| CONNECT/DISCONNECT/EXIT/QUIT | Old local handlers, with explicit limitations. | UI connect/disconnect works, but typed commands largely lack corresponding backend handling. | **P2:** connect command dispatcher to existing connection actions; preserve credential-entry policy. O5/N7/N8/B2 |
| WHENEVER SQLERROR | Old stores behavior and has an error-path check. | No equivalent handler found. | **P2:** define stop/continue and exit semantics, then test deliberately failing scripts. Do not copy the old regex without checking it. O5 |
| Typed aliases | Old has many handlers, but recognition and handlers are not uniformly complete. | New recognizer and backend supported-command set differ. | **P1:** one supported-command registry should drive completion, help, input completeness and dispatch. Unsupported commands should get a clear message instead of Oracle syntax errors. O5/N7/B2 |
| Transcript restore | Old persists transcript and restores it on reload. | Command history is kept in runtime arrays; SQL history is persisted separately. | **P2:** add optional per-user transcript persistence with size limits and clear controls. O5/N7 |
| Terminal source coloring/errors | Old colors prompts and marks relevant command ranges. | New renders submitted commands and errors as text blocks. | **P2:** port useful source markings while retaining the new output structure. O2/O5/N2 |

## 4. Results, files and object browsing

| Option | Old compiler | New compiler | Recommendation |
|---|---|---|---|
| Query result grid | Data display, custom column resizing and sorting helpers. | Plain DOM table in `renderTable()`. | **P2:** extract resizable/sortable grid behavior; keep original data types for sorting. O4/N1 |
| Data/Output separation | Old keeps output/result state and active tab. | Data tab can render both a table and output; execute directly calls `renderResult()`, regardless of active tab. | **P1:** render according to one active-tab state after every execution. Test execution while Output or History is selected. O4/N1/N2/N4 |
| Result copy/download/share | Export actions exist. | Selected-tab actions exist, including CSV export for data and text for output. | Keep new exports; add type-safe sorting and explicit truncation notices. O4/N4 |
| Row limit and truncation | Shared execution limit. | Same backend `MAX_ROWS=500`; response has `truncated` and `maxRows`, but main result render does not clearly surface the limit. | **P1/P2:** display a fetched-row limit notice. Do not label a capped result a complete export. B1/B2/N1 |
| Result persistence | Old persists last result/output and restores workspace state. | New persists history; no equivalent general result restoration found. | **P2:** add opt-in, bounded, per-user recovery for editor/result state. O6/N5 |
| Object categories/schema/filtering | Old navigator provides schema and category controls and property views. | New tree also provides categories, schema/filtering and details. | Keep new navigation; port only missing convenience behavior after side-by-side UI checks. O4/N1 |
| Object details | Old opens a dedicated properties dialog for structure/data/constraints. | New writes object details into the compiler result area. | **P2:** consider old non-destructive details panel/dialog so inspecting a table does not replace query results. O4/N1 |
| DDL navigator refresh | Old refresh helper can start refresh and optionally wait. | New calls refresh only when “Wait for Navigator refresh” is checked. | **P1:** separate “refresh after DDL” from “wait for refresh.” Unchecking Wait should not silently remove refresh unless explicitly intended. O3/O6/N2/N8 |
| Open local SQL file | Old file-picker workflow. | New local file inputs load SQL. | Already present; ensure line count, cursor, metrics, sharing and highlights update consistently. O4/N3 |
| Save SQL on server | Old uses `/api/files` and file-list dialogs. | New Save downloads `query.sql`; no corresponding server file browser in reviewed new UI. | **P2:** port file manager using existing isolated backend storage; distinguish “Save to workspace” and “Download.” O4/N3/B4 |
| Export chosen statement scope | Old chooses all/last/successful/unsuccessful statements and can include execution details. | New editor file actions operate on entire buffer; result/history exports are separate. | **P2:** port scope selector and explain whether it exports source or execution history. O4/N3/N4 |
| Preserve filename on download | Old tracks file name. | New downloads use fixed `query.sql`. | **P3:** retain loaded filename internally; no need to restore the filename label the user removed. O4/N3 |

## 5. Settings, accessibility and teaching features

| Option | Old compiler | New compiler | Recommendation |
|---|---|---|---|
| Settings matrix | Side-by-side terminal/editor settings: font, completion, metadata, clean code, commit, starters, synchronization. | Smaller settings page plus per-toolbar controls. | **P2:** reuse the organization, preserving stored preferences and current approved defaults. O0/N0/N8 |
| Sync terminal results with compiler | Old explicit optional preference. | New `execute()` updates shared result data for terminal executions. | **P2:** add an explicit preference while keeping teaching/history recording independent. O5/N2 |
| Fullscreen and navigation collapse | Supported. | Supported, with new sidebar/navigation mode. | Keep new behavior; add only missing ergonomic improvements. O6/N8 |
| Pane resizing | Old sidebar width and editor/results split controls. | New editor/results splitter; navigator collapse. | **P2:** consider a horizontal navigator-width splitter; preserve vertical editor scrollbar. O6/N0/N9 |
| Scroll jump controls | Old jump-to-edge and return controls. | No equivalent controls found. | **P3:** optional for long outputs. O6 |
| Shortcut help and access hints | Old detailed list and Alt-key access hints. | Global help trigger exists; compiler-specific shortcut help/target must be checked against rendered new markup. | **P2:** provide one accurate compiler shortcut dialog and test every listed key. A shared help button alone is not complete help. O6/O7/G1 |
| Popup placement | Old has anchored popup helpers. | Recent shared top-layer positioning covers starter/case/account/color menus. | Keep recent fixes; test inside fullscreen, narrow screens and dialogs. Do not replace them with old z-index-only styling. O4/G1 |
| Teacher/student SQL history | Old UI mainly local history, although shared execute backend records authenticated activity. | New dedicated history API UI, student filters, pagination and record operations. | **Keep new.** Never replace server-backed history with only localStorage history. O4/N5/B3 |
| Teacher editor access | No corresponding old workflow found. | Permission modes, student snapshots, teacher view/edit and synchronization. | **Keep new.** All migrated editor actions must trigger the same change/sharing hooks. N6/B3 |
| Typed/pasted/edited metrics | No equivalent old frontend metric tracking found. | New collects edit metrics and sends `editSource`. | **Keep new.** Migrated autocomplete/format/case/undo should not accidentally inflate manual-typing counts. N2/N6/B3 |
| Lab selection and heartbeat | Old has a lab-loading/heartbeat integration at the end of app.js; its lab selector is conditional on matching markup. | New loads lab context and sends activity heartbeats. | **Keep new.** Compare lab UI availability separately from the old conditional heartbeat integration; this is not wholly new backend functionality. O6/N6 |
| Saved Oracle credentials/reconnect | Old remembers connection workflow details. | New supports encrypted server profile credentials and reconnect flow. | Keep new server-backed behavior; keep login fields aligned with the current shared login policy. Browser autofill and server reconnect are distinct features. N8/B1/G1 |

## 6. Specific correctness risks before migration

1. **Statement targeting:** new `Run statement` sends the entire buffer. Reproduce with two harmless SELECT statements; inspect both the request and returned `statementsExecuted`.
2. **Mode flags do not select server behavior:** old sends `mode`, new sends `script`; current backend uses neither to constrain statement count. Fix contract and frontend selection together.
3. **Transformation safety:** new case conversion changes literal contents, and new formatting collapses literal whitespace. Old case token handling is useful; neither regex formatter should be adopted unchanged.
4. **Command recognition is broader than execution:** examples include SHOW, DEFINE, LIST and START in new SQL*Plus completeness recognition, versus the backend's much narrower client-command handlers.
5. **Inactive response hooks:** new spool, clear, prompt and SQL*Plus disconnect handling expect fields absent from the reviewed successful execution response. Decide where each feature actually lives.
6. **Result-tab state:** new execution renders the data-style result regardless of selected tab. Correct state management before extending results.
7. **Refresh semantics:** new Wait setting currently controls whether DDL refresh happens at all. Restore the distinction between scheduling a refresh and awaiting it.
8. **Old code is a donor, not a certified baseline:** statement scanning is not a complete Oracle parser, formatting is regex-based, VARIABLE storage is not proof of OUT-bind support, and some command checks/aliases are inconsistent.

These are source-level findings. Confirm them in a non-production schema before deciding final user-visible semantics.

## 7. Recommended implementation sequence

| Stage | Work | Dependencies and acceptance |
|---|---|---|
| 1 — Execution correctness | Statement targeting; selected SQL; one execution contract; bind dialog; in-flight guard; optional all-row-change warning. | Verify individual statement/selection/script behavior with SQL and PL/SQL. Bind cancellation sends no query. One user action creates one execution. Preserve backend privilege restrictions. |
| 2 — Safe editing | Shared tokenizer; literal-safe case conversion; formatter; find/replace; diagnostics. | Quoted identifiers, apostrophe escaping, comments, `q` quotes, semicolons in strings and multiline PL/SQL all survive unchanged where expected. |
| 3 — Core SQL*Plus parity | One command registry; SHOW/HELP/PROMPT/REMARK; variables/substitution; buffer commands; CLEAR; scripts. | Test completeness recognition and dispatch from the same command specification. Unsupported commands produce a useful message before Oracle execution. |
| 4 — Workspace productivity | Sort/resize grids; explicit truncation; file manager; source-scope exports; DDL refresh; optional transcript restore. | Preserve results while inspecting objects. Verify isolation of files/history between users and Oracle accounts. |
| 5 — Specialist compatibility | Spool, COLUMN/report formatting, titles, COMPUTE and advanced SQL*Plus settings. | Adopt only commands needed by users/classes. Publish and test the supported subset. |

No compiler features were migrated as part of this report. The order above is a proposal, not approval to change any previously accepted setting.

## 8. Suggested code organization

- `sql-tokenizer`: source offsets, strings, comments, identifiers and statement boundaries; one implementation shared by formatting, diagnostics and execution targeting.
- `editor-adapter`: read/write source, selections, scrolling, undo and change events; new compiler remains the host UI.
- `sqlplus-dispatcher`: command registry, buffer, substitution, settings and transcript; distinguish local commands from Oracle SQL.
- `execution-client`: typed request/response shape, binds, cancellation-before-submit, running state and error mapping; preserve `editSource` and teaching hooks.
- `result-grid` and `workspace-files`: reusable tables/exports and existing file API integration.

Avoid copying the complete old `app.js` into the new page. It attaches global listeners, assumes old element IDs and uses old localStorage keys. Its execution and persistence can conflict with new sharing, lab and history behavior.

## 9. Regression checklist for implementation

- Selected SQL, caret SQL, full script, anonymous block and stored-program compilation; include deliberate errors in the second statement.
- Named binds, cancellation, repeated binds, comments/literals containing `:` and `:=` assignment.
- Autocommit both ways, explicit COMMIT/ROLLBACK, and independence of editor/terminal connections. Use a disposable table and verify actual database state.
- Every supported client command, aliases, empty input, mixed scripts, unmatched quotes, alternative quoting and slash terminators.
- Invalid character mapping, font/zoom, textarea/highlight alignment, both scroll axes, narrow screen and fullscreen popup placement.
- Data/Output/History active tabs before and after a run, truncation notice, CSV escaping and result sorting for NULL/numeric/date values.
- File open/save/download, transcript recovery and account isolation; verify restoring state never reveals another user's source/results.
- Teacher permission modes and revocation; lab assignment and heartbeat; paste/manual-edit metrics through all new editor commands.
- Existing approved toolbar alignment, line-count placement, Clean code label, inline hidden-character boxes, vertical scrollbar and login/reset behavior remain intact.

## Source evidence index

Line numbers refer to files at the time of this report. Group references identify the relevant implementation starting points; minified old functions often occupy a whole line.

- **O0:** [Old workspace controls and settings matrix](C:/Users/Jeetendra/OneDrive/文档/New project/index.html:47)
- **O1:** [Old SQL completion context and suggestion engine](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:20)
- **O2:** [Old live diagnostics, bracket matching and highlighting](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:145)
- **O3:** [Old statement selection, binds, execution, warning and case conversion](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:190)
- **O4:** [Old result grids, history, navigator, workspace files and exports](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:166)
- **O5:** [Old SQL*Plus transcript, execution and client-command emulator](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:208)
- **O6:** [Old fullscreen, editor shortcuts, fonts, splitters and persistence](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:270)
- **O7:** [Old find/replace, normalization and shortcut help](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:326)
- **O8:** [Old Format button implementation](C:/Users/Jeetendra/OneDrive/文档/New project/app.js:260)
- **N0:** [New PL/SQL toolbar, editor and status controls](C:/Users/Jeetendra/OneDrive/文档/New project/templates/oracle_sql_workspace.html:71)
- **N1:** [New object details; result grid starts at line 432](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:208)
- **N2:** [New execution request, result rendering and response handling](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:483)
- **N3:** [New editor rendering, transformation, execution handlers and file actions](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:721)
- **N4:** [New result tabs and selected-tab exports](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:892)
- **N5:** [New history rows, server history loading and filtering](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:954)
- **N6:** [New teacher/student editor access and sharing; metrics and heartbeats at lines 13 and 149](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:1172)
- **N7:** [New terminal input, completeness, execution, history and paste settings](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:1303)
- **N8:** [New fullscreen, navigation, settings and connect actions; saved credentials at line 621](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide.js:1480)
- **N9:** [New editor viewport and scrollbar layout](C:/Users/Jeetendra/OneDrive/文档/New project/online-oracle-compiler/oracle-sql-ide-layout.css:104)
- **B1:** [Shared script splitter; bind mapping at line 250; connection timeout at line 197](C:/Users/Jeetendra/OneDrive/文档/New project/workbench/views.py:136)
- **B2:** [Shared execution endpoint and implemented SQL*Plus command subset](C:/Users/Jeetendra/OneDrive/文档/New project/workbench/views.py:562)
- **B3:** [Shared execution records, teacher history and editor-sharing APIs](C:/Users/Jeetendra/OneDrive/文档/New project/workbench/views.py:684)
- **B4:** [Shared saved SQL file APIs](C:/Users/Jeetendra/OneDrive/文档/New project/workbench/views.py:1019)
- **G1:** [Shared UI code; invisible-character overlay and anchored popup positioning follow](C:/Users/Jeetendra/OneDrive/文档/New project/global-controls.js:151)

Additional scope references: [Django route/view mapping](C:/Users/Jeetendra/OneDrive/文档/New project/workbench/views.py:338), [current startup description](C:/Users/Jeetendra/OneDrive/文档/New project/README.md:25).


## Follow-up: completion, indentation and automatic checking

Rechecked after the user specifically highlighted these behaviors.

| Behavior | Evidence in current old compiler | Migration requirement |
|---|---|---|
| Suggest keywords and identifiers | `getSqlToken`, `getSqlSuggestionContext` and `getSqlSuggestionItems`, app.js lines 20–106. Context narrows suggestions to tables, columns, functions, keywords, data types and constraints. | Preserve context-specific completion rather than offering the entire keyword list everywhere. |
| Suppress suggestions for data entry | Quoted strings yield no completion token. Context returns `none` inside VALUES lists, immediately after comparison operators, after DEFAULT and in datatype-size parentheses. These are heuristic rules, not a full Oracle grammar. | Add explicit tests for literals, INSERT values, predicate values, datatype sizes, comments and Oracle alternative quoting. Never suggest actual stored row data. |
| Automatic checking while typing | Input listeners debounce `validateSqlWhileTyping` by 2,000 ms (app.js lines 141 and 212). Checks cover likely keyword/function typos, unusual whitespace, parentheses and some metadata-based object-name mistakes. | Port as advisory live validation; this is not a server compilation request. |
| Oracle compilation | Requests occur on execution paths; no automatic database compilation on each input was found in the current old source. | Do not silently execute or compile stored programs while typing. If an explicit background validation mode is wanted, define its database effects separately. |
| Indentation | Tab inserts two spaces; Ctrl+[ / Ctrl+] indents/outdents lines. Bracket pairing and Format are separate features. No Enter-based indent inheritance or BEGIN/END-aware automatic indentation was found in the current old app.js. | Add true Enter-based auto-indentation as an explicit feature; do not label the existing Tab handler as automatic indentation. If a previous restore point had it, identify and compare that version. |

The initial broad report grouped several of these under autocomplete, shortcuts and diagnostics; this section makes their exact scope and limitations explicit. Compiler code is unchanged by this follow-up.


## Implementation follow-up: editor assistance

The new compiler now loads `editor-assist.js` for both inputs. Added keyword,
object and column-name suggestions; suppression in quoted data, VALUES,
comparison-value, comment and datatype-length contexts; Enter-based indentation;
and advisory local checks after a two-second pause. Metadata comes from the
structure API, not stored row data. Automatic Oracle execution/compilation while
typing is not enabled. These additions cover the subsequently discussed editor
assistance scope, not every proposed migration in this report.

Browser-autofill hints now enable on the username field at three characters and
request suppression on password focus. Native browser credential-picker timing
remains browser-controlled; no exact popup timing is guaranteed by site code.
