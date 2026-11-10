# SQL*Plus project checks

## Per-file isolation

Every new ProjectExecution gets a random PCHK_ schema and independent encrypted
password. The provisioning account is used only to call the broker; file SQL is
executed as the generated restricted user. Different users may check files concurrently without sharing database objects.
Resuming a paused check reuses its schema. Starting another file check cancels
that same user's unfinished checks, closes their sessions, and removes their
schemas. Failed cleanup is retried; other users' checks are unaffected. Completion removes only the completed check's schema and objects.
Idle checks expire after 30 minutes. Logs remain after schema removal.

Deployment requires migration 0061 and DBA installation of
`workbench/project_schema_broker.sql` inside the sandbox PDB. Replace
`__PROJECT_OWNER__` with the quoted provisioning username. This installation
requires explicit approval: it creates a NO AUTHENTICATION, locked broker with
CREATE USER and DROP USER privileges and CONNECT/RESOURCE WITH ADMIN OPTION.
It does not grant GRANT ANY ROLE. Only the provisioning account receives EXECUTE
on the broker package. Generated users receive CONNECT, RESOURCE and 100 MB on
USERS, with no access to the broker. Broker removal requires a matching run ID,
random schema name and original provisioning caller recorded in its private
ledger. It never deletes the old shared schema.

The user approved the revised broker on 2026-11-01. The migration and broker
installation have been applied in FREEPDB1 and verified end to end. Preserve
this approved permission model. New checks fail closed if provisioning is
unavailable; they do not fall back to a shared schema.

The worker sweeps persistent leases every 30 seconds, including after restart.
Run `python manage.py cleanup_project_schemas` for explicit cleanup/retries when
no worker is running. Failed cleanup retains its lease and error for retry.
An earlier shared-schema run cannot be resumed into a new empty schema after
some statements have already succeeded; start a new file check after upgrading.

Project checks use the server's native SQL*Plus client, not a partial command
emulator. Install SQL*Plus on the portal server and make `sqlplus` available on
PATH, or set `PROJECT_SQLPLUS_EXECUTABLE` to its absolute executable path.
Set `ORACLE_HOME` for a full Oracle installation if necessary; the runner can
infer it from the executable's parent directory when message files are present.
Restart the portal after updating its environment or code.

Start a new project check after upgrading. SQL, PL/SQL, SQL*Plus formatting,
DEFINE/UNDEFINE and substitutions, VARIABLE/EXEC/PRINT, SHOW, DESCRIBE,
report commands and buffer commands execute in a persistent native session.
DEFINE values in the file are supported. Missing substitution variables and
undeclared bind inputs pause the check and display a value form. Supply text
or numeric bind values and choose Use values and continue. Values stay in the
active check session; successful statements are not replayed. Native VARIABLE
declarations and EXEC assignments remain supported, including optional trailing
semicolons. Supplied values are checked before execution and never disable the
compiler security policy. Output is recorded per
statement and rendered as plain text, including when MARKUP is enabled.
EXIT/QUIT stops the project and respects the native commit/rollback clause.
AUTOCOMMIT starts ON, matching the previous runner's per-statement commits;
scripts can change it. Uncommitted work is rolled back when a session is closed
or expires. A failed command pauses execution and keeps the session for retries.

This is a shared database sandbox, not a server terminal. SQL*Plus runs with
`-RESTRICT 3`: HOST/!, EDIT, GET, SAVE, SPOOL, START/@/@@ and STORE cannot access
the portal host. CONNECT, DISCONNECT, PASSWORD and COPY cannot change the shared
connection. ACCEPT, bare INPUT and PAUSE require an interactive client and are
unavailable in uploaded files. WHENEVER and command delimiter changes cannot
override project progress tracking. Command names must be literal; substitution
is supported in SQL and command arguments. Oracle permissions still determine
which database operations are allowed. Do not grant DBA privileges to the
shared project account.

Sandbox configuration, initial login and resume enforce the compiler's existing
account policy: SYS/SYSTEM/SYSDBA logins are rejected, CONNECT and RESOURCE must
be granted, and elevated system privileges are rejected. Invalid configurations
are not saved. Account grants are never changed automatically.

Project files use the compiler's shared `blocked_admin_command` check, including
its checks of dynamic SQL strings. The full file is checked before a run is
created. Substitution values are resolved and checked before execution with
native substitution disabled for that command. RUN and / are checked against
the current SQL buffer, including edits. Administrative SQL, GRANT/REVOKE,
SET ROLE and native instance-management commands remain blocked. These checks
complement the Oracle account's permissions; they are not a replacement for
database privilege enforcement.

Admission is queued persistently in the portal database. Teachers and administrators
can set student active limits from 0–35. Changing the teacher allocation requires
administrator rights, including when using the Teacher workspace; their combined total
must be 1–35. Migration preserves the previous combined limit (30 becomes
5 teacher + 25 student slots). A zero group limit keeps new checks in that group
queued until its capacity is increased. Lowering limits never interrupts active
checks. Paused checks with a live schema retain their slot until resumed,
completed, replaced or expired. The same-user replacement rule includes queued
checks.

Slot admission is serialized using the configuration row lock, so the combined
active reservation count cannot exceed 35 across workers. Teachers are admitted
before eligible students, FIFO within each group, without preempting running
checks. Each role stays within its own configured allocation; unused slots are
not borrowed from the other group. The scheduler retries admission on completion,
cleanup, limit changes and its regular wakeup. The page polls queued/running
checks automatically. A dedicated pool of up to 35 project workers runs checks
outside the HTTP request workers. The standard run_server launcher starts the
scheduler; other hosts should call project_queue.start_scheduler at worker startup.

Native SQL*Plus sessions remain process-local and expire after 30 minutes idle.
Each command has a 5-second timeout and a 200,000-character output limit.
Use one portal worker, or route project resume requests to the same worker as
the original request. If that worker restarts or a session expires, the user must
start a new check: successful DDL/DML is never replayed automatically. Previously
committed objects remain in the shared schema. A concurrent resume request is
rejected rather than executing the same statements twice.

Run `python manage.py test workbench.tests_project_runner --noinput` to check
script splitting, native client state, restricted commands and project progress.
Native tests do not require database credentials. Database integration additionally
requires verifying the administrator-configured sandbox with SHOW USER, DESC,
and session-only bind variable commands.

## Refresh recovery

File submission queues execution in a server-side commit callback; it does not
need a second browser request to begin. Refresh and F5 are read-only: they restore
the current user's latest saved check, original filename/source, statements,
results, queue status and pending variable requests. Polling reconnects to the
same run. Successful statements are never replayed by page restoration.

Unsaved project names, corrections and variable input fields are kept in
user-scoped sessionStorage. The selected File is stored in user-scoped IndexedDB
and restored to the file picker; for submitted files the server's saved source
is a fallback. Database connection passwords and capacity configuration are not
stored in browser drafts. These controls do not change the existing schema
expiry or same-user new-file replacement policies.

## Tests and daily-task sandboxes

Teachers can enter optional sample table/data SQL when creating a test, or edit
it under Sample table setup on an existing test. Students open Test sandbox from
Tests, or Daily-task sandbox from Daily tasks. Setup executes once per new schema.
The sandbox remains active between answer executions and uses the same queue,
quotas, SQL policy, variable prompts, result tables and teacher-report access as
file checks. Result-based grading connects to the assessment schema, never the
student's saved compiler account. An answer must have executed successfully in
the test sandbox before it can be submitted through a test submission route.

The workspace saves SQL drafts per question, selected question and executed
answers. Refresh restores the same activity and does not rerun setup. While an
assessment page is open, an owner-only heartbeat retains its native session;
teacher report reads do not keep a student's sandbox alive. Submission releases
the schema after grading, while answers, grades and the execution report remain.

Starting any new sandbox activity stops the user's earlier file/test/daily-task
sandbox, cancels its queue entry and cleans its schema. Unsubmitted test attempts
are marked suspended; saved drafts, answers and reports are retained. Reopening
a stopped activity displays saved work and requires Start sandbox to allocate a
fresh schema; a refresh of the currently active activity never does this.
