"""Compare isolated Oracle executions from identical teacher-supplied fixtures.

Never evaluate reference code in a student's already modified sandbox. Every side
gets a broker-provisioned schema with the same setup, then is removed in finally.
"""
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
import json
import re
import time as clock

from .project_sqlplus import CLIENT_COMMAND, validate_project_command
from .sql_lexer import bind_names, is_plsql_statement, split_statements, tokens

MAX_ROWS = 5000
MAX_STATEMENTS = 100
MAX_OBJECTS = 100
MAX_RESULT_BYTES = 2000000
MAX_SECONDS = 30


class GradingUnavailable(ValueError):
    """An infrastructure/reference limitation must never become a wrong answer."""


def statements(source):
    if not isinstance(source, str) or len(source) > 200000:
        raise ValueError("Use SQL text up to 200,000 characters.")
    result = []
    for text in split_statements(source, sqlplus=True, client_command=CLIENT_COMMAND):
        parts = [part for part in tokens(text) if part["kind"] != "comment"]
        if not parts or text.strip() == "/":
            continue
        validate_project_command(text)
        command = text[parts[0]["start"]:].strip()
        first = parts[0]["text"].upper()
        if first in {"EXEC", "EXECUTE"}:
            command = "BEGIN " + command.split(None, 1)[1].rstrip("; ") + "; END;"
        elif first == "SET" and re.match(r"SET\s+SERVEROUTPUT\s+ON\b", command, re.I):
            continue
        elif first not in {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME", "COMMENT", "BEGIN", "DECLARE", "CALL", "COMMIT", "ROLLBACK", "SAVEPOINT", "SET"}:
            raise ValueError("Execution grading accepts Oracle SQL and PL/SQL, not interactive SQL*Plus or operating-system commands.")
        if bind_names(command):
            raise ValueError("Use literal test values rather than unresolved bind variables in graded SQL.")
        if any(part["text"] in {"&", "&&"} for part in parts):
            raise ValueError("Use literal test values rather than interactive substitution variables.")
        result.append(command)
    if len(result) > MAX_STATEMENTS:
        raise ValueError("Automatic execution grading supports at most 100 statements per script.")
    return result


def needs_verification(source):
    for statement in statements(source):
        words = [part["text"].upper() for part in tokens(statement) if part["kind"] == "word"]
        if words and words[0] == "CREATE" and any(word in {"PROCEDURE", "FUNCTION", "PACKAGE", "TRIGGER", "TYPE"} for word in words[1:5]):
            return True
    return False


def validate_reference(reference, verification="", setup=""):
    if not statements(reference):
        raise ValueError("Add a reference SQL answer for execution grading.")
    statements(setup)
    statements(verification)
    if needs_verification(reference) and not verification.strip():
        raise ValueError("Add verification SQL that calls the stored program or fires the trigger and checks its behavior.")


def _clean_text(value, schema):
    return str(value).replace(schema, "<SCHEMA>").replace(schema.lower(), "<SCHEMA>")


def normalize(value, schema=""):
    if value is None:
        return ["null", ""]
    if hasattr(value, "read"):
        value = value.read(amount=200001)
        if len(value) > 200000:
            raise GradingUnavailable("A result value exceeds the automatic grading size limit.")
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return ["number", format(Decimal(str(value)).normalize(), "f")]
    if isinstance(value, (datetime, date, time)):
        return ["date" if isinstance(value, (date, datetime)) else "time", value.isoformat()]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    return [type(value).__name__, _clean_text(value, schema) if schema else str(value)]


def expression(value, schema):
    if value is None:
        return None
    if hasattr(value, "read"):
        value = value.read()
    value = re.sub(r"ISEQ\$\$_[0-9]+", "IDENTITY_SEQUENCE", str(value))
    text = str(value).replace('"'+schema+'".', '').replace(schema+'.', '')
    return [(part["kind"], part["text"].upper() if part["kind"] == "word" else part["text"]) for part in tokens(text) if part["kind"] != "comment"]


def _query(connection, sql, parameters=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters or {})
        rows = cursor.fetchmany(MAX_ROWS + 1)
        if len(rows) > MAX_ROWS:
            raise GradingUnavailable("Automatic grading supports at most 5,000 rows per table or query.")
        return rows


def _collect_rows(cursor, schema):
    rows, size = [], 0
    while True:
        batch = cursor.fetchmany(50)
        if not batch:
            break
        for row in batch:
            encoded = json.dumps([normalize(value, schema) for value in row], ensure_ascii=False, separators=(",", ":"))
            size += len(encoded.encode("utf-8"))
            rows.append(encoded)
            if len(rows) > MAX_ROWS or size > MAX_RESULT_BYTES:
                raise GradingUnavailable("Grading results exceed the 5,000-row / 2 MB limit.")
    return rows


def _rows(connection, sql, schema, ordered=False):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        rows = _collect_rows(cursor, schema)
    return rows if ordered else sorted(rows)


def _identifier(name):
    return '"' + name.replace('"', '""') + '"'


def _deadline(deadline):
    if clock.monotonic() > deadline:
        raise GradingUnavailable("Execution grading exceeded its time limit. Reduce the sample data or verification script.")


def _implicit_results(cursor):
    try:
        return cursor.getimplicitresults()
    except Exception as exc:
        # python-oracledb uses DPY-1004 when successful PL/SQL returned no cursor.
        if str(exc).startswith("DPY-1004:"):
            return []
        raise


def _execute(connection, script, schema, deadline):
    results, output = [], []
    with connection.cursor() as cursor:
        for statement in statements(script):
            _deadline(deadline)
            sql = statement.strip()
            if not is_plsql_statement(sql):
                sql = re.sub(r";\s*$", "", sql)
            sql = re.sub(r"\n\s*/\s*$", "", sql)
            cursor.execute(sql)
            if cursor.description:
                count = len(cursor.description)
                rows = _collect_rows(cursor, schema)
                words = [part["text"].upper() for part in tokens(sql) if part["kind"] == "word"]
                ordered = any(a == "ORDER" and b == "BY" for a, b in zip(words, words[1:]))
                results.append((count, rows, ordered))
            if is_plsql_statement(sql):
                for implicit in _implicit_results(cursor):
                    try:
                        results.append((len(implicit.description), _collect_rows(implicit, schema), False))
                    finally:
                        implicit.close()
        line = cursor.var(str, size=32767)
        status = cursor.var(int)
        for _ in range(MAX_ROWS + 1):
            _deadline(deadline)
            cursor.callproc("DBMS_OUTPUT.GET_LINE", [line, status])
            if status.getvalue() != 0:
                break
            output.append(_clean_text(line.getvalue() or "", schema))
        else:
            raise GradingUnavailable("DBMS_OUTPUT exceeds the automatic grading line limit.")
    return {"queries": results, "output": output}


def _snapshot(connection, schema, deadline):
    def query(sql):
        _deadline(deadline)
        return _query(connection, sql)
    objects = query("SELECT object_name, object_type, status FROM user_objects WHERE object_name NOT LIKE 'BIN$%' AND subobject_name IS NULL ORDER BY object_type, object_name")
    if len(objects) > MAX_OBJECTS:
        raise GradingUnavailable("Automatic grading supports at most 100 database objects.")
    supported = {"TABLE", "INDEX", "SEQUENCE", "VIEW", "MATERIALIZED VIEW", "PROCEDURE", "FUNCTION", "PACKAGE", "PACKAGE BODY", "TRIGGER", "TYPE", "TYPE BODY", "LOB", "SYNONYM"}
    if any(kind not in supported for _, kind, _ in objects):
        raise GradingUnavailable("This object type needs a teacher-provided verification strategy.")
    snapshot = {"objects": [(name, kind, status) for name, kind, status in objects if kind not in {"INDEX", "LOB"} and not name.startswith("ISEQ$$_")]}
    if not objects:
        return snapshot
    kinds = {kind for _, kind, _ in objects}
    if kinds & {"PROCEDURE", "FUNCTION", "PACKAGE", "PACKAGE BODY", "TRIGGER", "TYPE", "TYPE BODY"}:
        if query("SELECT name, type, line, position FROM user_errors WHERE attribute = 'ERROR'"):
            raise ValueError("A stored object did not compile successfully.")
        snapshot["arguments"] = query("SELECT object_name, package_name, overload, argument_name, position, sequence, data_type, in_out FROM user_arguments ORDER BY object_name, package_name, overload, sequence")
    table_names = [name for name, kind, _ in objects if kind in {"TABLE", "VIEW", "MATERIALIZED VIEW"}]
    literal = lambda text: "'" + text.replace("'", "''") + "'"
    table_filter = "(" + ",".join(literal(name) for name in table_names) + ")"
    if table_names:
        columns = defaultdict(list)
        for table, column, dtype, length, precision, scale, nullable, default, virtual in query("SELECT table_name, column_name, data_type, data_length, data_precision, data_scale, nullable, data_default, virtual_column FROM user_tab_cols WHERE table_name IN " + table_filter + " AND hidden_column = 'NO' ORDER BY table_name, column_id"):
            columns[table].append((column, dtype, length, precision, scale, nullable, expression(default, schema), virtual))
        snapshot["columns"] = dict(columns)
        snapshot["data"] = {}
        for table in table_names:
            _deadline(deadline)
            names = [row[0] for row in columns[table]]
            snapshot["data"][table] = _rows(connection, "SELECT " + ",".join(_identifier(name) for name in names) + " FROM " + _identifier(table), schema)
            if sum(len(row.encode("utf-8")) for rows in snapshot["data"].values() for row in rows) > MAX_RESULT_BYTES * 2:
                raise GradingUnavailable("The schema snapshot exceeds the 4 MB grading limit.")
        cons_columns = defaultdict(list)
        for name, column, position in query("SELECT constraint_name, column_name, position FROM user_cons_columns WHERE table_name IN " + table_filter + " ORDER BY constraint_name, position"):
            cons_columns[name].append(column)
        constraints = query("SELECT constraint_name, table_name, constraint_type, search_condition, r_constraint_name, delete_rule, status, deferrable, deferred, index_name FROM user_constraints WHERE table_name IN " + table_filter)
        identities = {row[0]: (row[1], cons_columns[row[0]]) for row in constraints}
        semantic = []
        for name, table, kind, condition, reference, delete_rule, status, deferrable, deferred, index in constraints:
            semantic.append((table, kind, cons_columns[name], expression(condition, schema), identities.get(reference), delete_rule, status, deferrable, deferred))
        snapshot["constraints"] = sorted(semantic, key=repr)
        index_cols = defaultdict(list)
        for name, column, descending in query("SELECT index_name, column_name, descend FROM user_ind_columns WHERE table_name IN " + table_filter + " ORDER BY index_name, column_position"):
            index_cols[name].append((column, descending))
        index_exprs = defaultdict(list)
        for name, position, value in query("SELECT index_name, column_position, column_expression FROM user_ind_expressions WHERE table_name IN " + table_filter):
            index_exprs[name].append((position, expression(value, schema)))
        generated = {row[-1] for row in constraints if row[-1]}
        snapshot["indexes"] = sorted([(table, unique, kind, index_cols[name], sorted(index_exprs[name])) for name, table, unique, kind in query("SELECT index_name, table_name, uniqueness, index_type FROM user_indexes WHERE table_name IN " + table_filter) if name not in generated and kind != "LOB"], key=repr)
        snapshot["table_comments"] = query("SELECT table_name, comments FROM user_tab_comments WHERE table_name IN " + table_filter + " ORDER BY table_name")
        snapshot["column_comments"] = query("SELECT table_name, column_name, comments FROM user_col_comments WHERE table_name IN " + table_filter + " ORDER BY table_name, column_name")
    if "SEQUENCE" in kinds:
        snapshot["sequences"] = [row for row in query("SELECT sequence_name, min_value, max_value, increment_by, cycle_flag, order_flag, cache_size, last_number FROM user_sequences ORDER BY sequence_name") if not row[0].startswith("ISEQ$$_")]
        if any(name.startswith("ISEQ$$_") for name, _, _ in objects):
            snapshot["identities"] = query("SELECT table_name, column_name, generation_type, identity_options FROM user_tab_identity_cols ORDER BY table_name, column_name")
    if "SYNONYM" in kinds:
        snapshot["synonyms"] = [(name, table if owner == schema else owner + "." + table, link) for name, owner, table, link in query("SELECT synonym_name, table_owner, table_name, db_link FROM user_synonyms ORDER BY synonym_name")]
    if "TRIGGER" in kinds:
        snapshot["triggers"] = query("SELECT trigger_name, trigger_type, triggering_event, table_name, status FROM user_triggers ORDER BY trigger_name")
    return snapshot


def execute_isolated(user, source, setup="", verification=""):
    from .models import ProjectExecution, ProjectRunnerConfiguration
    from .project_schemas import file_credentials, remove_schema
    from .views import connect_options, open_oracle_connection
    config = ProjectRunnerConfiguration.objects.select_related("sandbox_connection").get(pk=1)
    run = ProjectExecution.objects.create(owner=user, name="Automatic execution grading", activity_kind="grading", sandbox_connection=config.sandbox_connection, source_text="", statements=[], status="running", slot_reserved=False)
    connection = None
    try:
        schema, password, dsn = file_credentials(run)
        connection = open_oracle_connection(connect_options({"user": schema, "password": password, "connectString": dsn}))
        connection.call_timeout = 5000
        connection.autocommit = False
        deadline = clock.monotonic() + MAX_SECONDS
        with connection.cursor() as cursor:
            cursor.execute("BEGIN DBMS_OUTPUT.ENABLE(200000); END;")
            cursor.execute("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'")
            cursor.execute("ALTER SESSION SET NLS_NUMERIC_CHARACTERS = '.,'")
        _execute(connection, setup, schema, deadline)
        connection.commit()
        connection.call_timeout = 2000
        result = _execute(connection, source, schema, deadline)
        verification_result = _execute(connection, verification, schema, deadline)
        connection.call_timeout = 5000
        snapshot = _snapshot(connection, schema, deadline)
        return {"execution": result, "verification": verification_result, "state": snapshot}
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        run.refresh_from_db()
        removed = remove_schema(run)
        ProjectExecution.objects.filter(pk=run.pk).update(status="completed", slot_reserved=False)
        if removed is False:
            # Existing cleanup worker retries retained leases; never claim a clean grade.
            raise GradingUnavailable("Temporary grading schema cleanup is pending. Please retry grading later.")


def grade_question(user, question, candidate, setup=""):
    verification = getattr(question, "verification_sql", "") or ""
    try:
        validate_reference(question.expected_sql, verification, setup)
        expected = execute_isolated(user, question.expected_sql, setup, verification)
    except Exception as exc:
        raise GradingUnavailable("Reference execution could not be evaluated. Check the sample setup, verification SQL and grading service.") from exc
    if not candidate.strip():
        return {"is_correct": False, "score": Decimal("0"), "feedback": "No SQL answer was submitted."}
    try:
        actual = execute_isolated(user, candidate, setup, verification)
    except GradingUnavailable:
        raise
    except Exception as exc:
        # Lost connections and execution timeouts are not proof of a wrong answer.
        text = str(exc)
        if re.search(r"(?:DPY-(?:1001|4011|4024)|ORA-(?:01013|03113|03114|03135|12537|12547|12570|12571))", text):
            raise GradingUnavailable("Automatic grading was interrupted. The answer is awaiting review.") from exc
        return {"is_correct": False, "score": Decimal("0"), "feedback": "The submitted SQL could not complete with the supplied setup and verification checks."}
    correct = equivalent(expected, actual)
    return {"is_correct": correct, "score": question.marks if correct else Decimal("0"),
            "feedback": "Execution output, verification checks and database state match the reference." if correct else "Execution completed, but its output, verification checks or database state differ from the reference."}


def equivalent(expected, actual):
    if expected["state"] != actual["state"]:
        return False
    for phase in ("execution", "verification"):
        reference, candidate = expected[phase], actual[phase]
        if reference["output"] != candidate["output"] or len(reference["queries"]) != len(candidate["queries"]):
            return False
        for (columns, rows, ordered), (actual_columns, actual_rows, _) in zip(reference["queries"], candidate["queries"]):
            if columns != actual_columns:
                return False
            if (rows if ordered else sorted(rows)) != (actual_rows if ordered else sorted(actual_rows)):
                return False
    return True
