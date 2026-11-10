# Execution grading for Daily Tasks and Tests

Choose **Compare execution results (SQL / PL/SQL)**. Supply a reference SQL answer and deterministic sample-table setup. Reference and student answers run in separate newly provisioned schemas with the same setup. The grader compares returned rows (including duplicates and reference-required ordering), DBMS_OUTPUT, table contents, columns/defaults, constraints, indexes, sequences, object status and verification results. It does not compare the SQL text.

## Stored programs and triggers

Verification SQL is required for CREATE PROCEDURE, FUNCTION, PACKAGE, TRIGGER and TYPE. Compilation alone cannot establish correctness. Verification should exercise normal cases, edge cases and side effects.

Example setup:

```sql
CREATE TABLE marks (id NUMBER PRIMARY KEY, score NUMBER);
INSERT INTO marks VALUES (1, 10);
INSERT INTO marks VALUES (2, 20);
```

Reference answer:

```sql
CREATE OR REPLACE PROCEDURE add_mark AS
BEGIN
  UPDATE marks SET score = score + 1;
END;
```

Verification SQL:

```sql
BEGIN add_mark; END;
/
SELECT id, score FROM marks ORDER BY id;
```

A different implementation passes if its tested behavior and final schema/data match. A procedure that merely compiles and does nothing fails these checks. Use additional calls and input rows to test the required behavior adequately. Each question starts from the shared sample setup; do not assume earlier questions created its tables.

## Boundaries

Only statements supported by the existing restricted Oracle sandbox can run. Interactive SQL*Plus inputs, operating-system commands and administrator/privilege commands remain excluded. Use literal inputs rather than unresolved binds/substitution prompts. Nondeterministic values such as current time and random numbers need deterministic fixtures/verification; automatic grading is based on the supplied cases, not proof of equivalence for every possible input.

Limits: 100 statements per script, 100 objects, 5,000 rows / 2 MB per result set, 4 MB per schema snapshot and bounded execution time. Infrastructure failures, invalid references, cleanup problems and exceeded bounds leave grading pending rather than marking an answer wrong. Temporary schemas are removed after each execution; the existing lease cleanup worker retries failed cleanup. Previously saved student grades are not automatically changed.

Validation: `python -B tools/check-execution-grading.py` runs live Oracle integration checks using only temporary broker-managed schemas. Unit regressions are in `workbench/tests_execution_grading.py`.
