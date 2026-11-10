# Advanced SQL workbench

Added locally hosted Monaco 0.52.2 and ExcelJS 4.4.0, retaining their licenses.

Both SQLcl input and workbook now support minimaps, sticky scope headings, bracket depth colors/guides, matching, folding, multiple cursors and column selection. Existing execution, history, draft and clean-paste handlers remain the execution source. Read-only state is mirrored from the original textarea.

Workbook tools include a second reference editor, an editable side-by-side diff for SQL or fetched output, and searchable built-in/personal snippets. Personal snippets are stored per user in browser storage. Shift+Alt+F formats SQL with nested indentation. Table/column completion and hover use the existing authorized object metadata endpoints; foreign key suggestions include referenced columns when metadata is available. Metadata is cached for 60 seconds and invalidated by schema identity.

Result grids provide search, column filters, numeric-aware sorting, pagination, CSV and genuine XLSX downloads. They operate on fetched rows, not the entire database table. SQLcl text output also offers an expandable interactive data grid. No hidden database queries run when sorting or filtering.

Validation: headless Chrome fixture and actual rendered workspace with mocked API responses; editor run shortcut isolation, drafts, formatting, alias column suggestions, multiple cursors, split/diff, snippets, grid filtering/sorting, XLSX download, dark/light layouts. Django static-serving and metadata-query regression checks. Oracle execution itself remains covered by existing execution logic; browser fixture runs do not execute SQL against live schemas.
