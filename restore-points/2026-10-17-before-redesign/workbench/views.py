import base64
import hashlib
import json
import os
import re
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock

import oracledb
from django.conf import settings
from django.http import FileResponse, JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

ROOT = Path(settings.BASE_DIR)
SQL_FILES = ROOT / "sql-files"
MAX_BODY = 256 * 1024
MAX_ROWS = 500
ORACLE_CALL_TIMEOUT_MS = 5000
IDLE_SECONDS = 30 * 60
_oracle_sessions = {}
_connect_attempts = {}
_lock = RLock()
_STATIC_FILES = {"index.html", "app.js", "styles.css", "output.css"}


def json_error(message, status=400, **extra):
    return JsonResponse({"error": str(message), **extra}, status=status)


def read_json(request):
    if len(request.body) > MAX_BODY:
        raise ValueError("Request is too large.")
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Invalid JSON request.")


def same_origin(request):
    from urllib.parse import urlparse
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        # Modern browsers provide Fetch Metadata even when a privacy policy
        # omits both Origin and Referer. Accept only an explicit same-origin
        # request in that case.
        return request.headers.get("Sec-Fetch-Site", "").lower() == "same-origin"
    parsed = urlparse(origin)
    # This app terminates TLS in its own WSGI server. Use that configured
    # transport as the source of truth if a server adapter misreports
    # wsgi.url_scheme.
    expected_scheme = "https" if settings.TLS_ENABLED or request.is_secure() else "http"
    if parsed.scheme.lower() != expected_scheme or parsed.username or parsed.password or not parsed.hostname:
        return False

    request_url = urlparse(f"{expected_scheme}://{request.get_host()}")
    try:
        expected_port = request_url.port or (443 if expected_scheme == "https" else 80)
        origin_port = parsed.port or (443 if expected_scheme == "https" else 80)
    except ValueError:
        return False
    if origin_port != expected_port:
        return False

    origin_host = parsed.hostname.rstrip(".").lower()
    request_host = (request_url.hostname or "").rstrip(".").lower()
    if origin_host == request_host:
        return True

    # The local service is intentionally reachable through several configured
    # names (loopback and JEET-PC). Accept those aliases when they use the same
    # scheme and port; arbitrary cross-site origins remain blocked.
    allowed_hosts = {host.lstrip(".").rstrip(".").lower() for host in settings.ALLOWED_HOSTS if host != "*"}
    return origin_host in allowed_hosts


def current_oracle_session(request, touch=True):
    key = request.session.session_key
    if not key:
        return None
    now = time.time()
    with _lock:
        expired = [sid for sid, value in _oracle_sessions.items() if now - value["last_activity"] > IDLE_SECONDS]
        for sid in expired:
            old = _oracle_sessions.pop(sid, None)
            if old:
                for connection_name in ("sqlplus", "editor"):
                    if old.get(connection_name):
                        try: old[connection_name].close()
                        except Exception: pass
        session = _oracle_sessions.get(key)
        if session and touch:
            session["last_activity"] = now
        return session


def is_default_connection(connect_string, user):
    return str(user or "").strip().upper() == "JIT25B_SCHEMA_YV521" or (
        "db.freesql.com" in str(connect_string or "").lower()
        and "2484" in str(connect_string or "")
        and "26ai_un3c1" in str(connect_string or "").lower()
    )


def safe_sql_filename(value):
    name = Path(str(value or "").strip()).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    if not name or name in {".", ".."}:
        raise ValueError("Enter a valid SQL file name.")
    return name if name.lower().endswith(".sql") else name + ".sql"


def sql_folder(session):
    settings_data = session["settings"]
    key = "\0".join((settings_data["connectString"].upper(), settings_data["user"].upper(), session["workspace_id"]))
    folder = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return SQL_FILES / folder


def split_script(source):
    statements, current = [], []
    single = quoted = line_comment = block_comment = False
    plsql = False
    for line in source.splitlines():
        if plsql and re.match(r"^\s*/\s*$", line):
            if "".join(current).strip(): statements.append("".join(current).strip())
            current, plsql = [], False
            continue
        current_text = "".join(current).strip()
        if not current_text:
            plsql = bool(re.match(r"^\s*(DECLARE|BEGIN)\b|^\s*CREATE(?:\s+OR\s+REPLACE)?\s+(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b", line, re.I))
        line_comment = False
        i = 0
        while i < len(line):
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if line_comment:
                current.append(ch); i += 1; continue
            if block_comment:
                current.append(ch)
                if ch == "*" and nxt == "/": current.append(nxt); i += 1; block_comment = False
                i += 1; continue
            if single:
                current.append(ch)
                if ch == "'" and nxt == "'": current.append(nxt); i += 1
                elif ch == "'": single = False
                i += 1; continue
            if quoted:
                current.append(ch)
                if ch == '"' and nxt == '"': current.append(nxt); i += 1
                elif ch == '"': quoted = False
                i += 1; continue
            if ch == "-" and nxt == "-": current.extend((ch, nxt)); i += 2; line_comment = True; continue
            if ch == "/" and nxt == "*": current.extend((ch, nxt)); i += 2; block_comment = True; continue
            if ch == "'": single = True; current.append(ch); i += 1; continue
            if ch == '"': quoted = True; current.append(ch); i += 1; continue
            if ch == ";" and not plsql:
                statement = "".join(current).strip()
                if statement: statements.append(statement)
                current = []; i += 1; continue
            current.append(ch); i += 1
        current.append("\n")
    tail = "".join(current).strip()
    if tail: statements.append(re.sub(r";\s*$", "", tail))
    return [item for item in statements if item]


def open_oracle_connection(options):
    connection = oracledb.connect(**options)
    connection.call_timeout = ORACLE_CALL_TIMEOUT_MS
    return connection


def _sql_tokens(sql):
    """Return SQL words and quoted string contents, skipping SQL comments."""
    tokens, i = [], 0
    while i < len(sql):
        if sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            i = len(sql) if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = len(sql) if end < 0 else end + 2
        elif sql[i] == "'":
            i += 1; value = []
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'": value.append("'"); i += 2; continue
                    i += 1; break
                value.append(sql[i]); i += 1
            tokens.append(("STRING", "".join(value)))
        elif sql[i].isalpha() or sql[i] == "_":
            start = i; i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in "_$#"): i += 1
            tokens.append(("WORD", sql[start:i].upper()))
        elif sql[i].isspace(): i += 1
        else: tokens.append(("PUNCT", sql[i])); i += 1
    return tokens


def blocked_admin_command(sql, depth=0):
    tokens = _sql_tokens(sql)
    words = [value for kind, value in tokens if kind == "WORD"]
    if words:
        first = words[0]
        second = words[1] if len(words) > 1 else ""
        third = words[2] if len(words) > 2 else ""
        if first in {"GRANT", "REVOKE"}: return first
        if first == "SET" and second == "ROLE": return "SET ROLE"
        if first == "CREATE" and ((second in {"USER", "ROLE", "PROFILE", "DATABASE"}) or (second == "PLUGGABLE" and third == "DATABASE")): return "CREATE " + (second + (" DATABASE" if second == "PLUGGABLE" else ""))
        if first == "ALTER" and second in {"SYSTEM", "DATABASE", "USER", "ROLE", "PROFILE"}: return "ALTER " + second
        if first == "DROP" and (second in {"DATABASE", "USER", "ROLE", "PROFILE", "TABLESPACE"} or (second == "PLUGGABLE" and third == "DATABASE")): return "DROP " + (second + (" DATABASE" if second == "PLUGGABLE" else ""))
    if depth < 3:
        for kind, value in tokens:
            if kind == "STRING":
                blocked = blocked_admin_command(value, depth + 1)
                if blocked: return blocked
    return None


def driver_binds(sql, supplied):
    supplied = supplied if isinstance(supplied, dict) else {}
    by_upper = {str(key).upper(): value for key, value in supplied.items()}
    names = set()
    pattern = re.compile(r"--[^\r\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|:([A-Za-z][A-Za-z0-9_$#]*)")
    for match in pattern.finditer(sql):
        if match.group(1): names.add(match.group(1).upper())
    values = {}
    for name in names:
        if name not in by_upper: raise ValueError(f"No value was supplied for bind variable :{name}.")
        values[name] = by_upper[name]
    return values


def json_value(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, bytes): return base64.b64encode(value).decode("ascii")
    if isinstance(value, (str, int, float, bool)) or value is None: return value
    return str(value)


def connect_options(data):
    username = str(data.get("user", "")).strip().upper()
    if re.fullmatch(r"(?:SYS|SYSTEM)(?:\s+AS\s+SYS(?:DBA|OPER))?", username) or data.get("admin") is True:
        raise ValueError("SYS, SYSTEM, and SYSDBA logins are disabled. Use a dedicated restricted schema account.")
    options = {"user": data["user"], "password": data["password"], "dsn": data["connectString"]}
    return options


def connect_info(connection, connect_string, requested_user):
    with connection.cursor() as cursor:
        cursor.execute("SELECT SYS_CONTEXT('USERENV','SERVICE_NAME'), USER FROM dual")
        service, user = cursor.fetchone()
        cursor.execute("SELECT granted_role FROM user_role_privs")
        roles = {row[0].upper() for row in cursor.fetchall()}
        cursor.execute("SELECT role FROM session_roles")
        roles.update(row[0].upper() for row in cursor.fetchall())
        cursor.execute("SELECT privilege FROM session_privs")
        privileges = {row[0].upper() for row in cursor.fetchall()}
        cursor.execute("SELECT privilege FROM user_sys_privs")
        direct_privileges = {row[0].upper() for row in cursor.fetchall()}
        privileges.update(direct_privileges)
        cursor.execute("SELECT privilege FROM role_sys_privs WHERE role IN (SELECT granted_role FROM user_role_privs)")
        privileges.update(row[0].upper() for row in cursor.fetchall())
    permitted_roles = {"CONNECT", "RESOURCE"}
    elevated_privileges = {"ALTER SYSTEM", "ALTER DATABASE", "CREATE USER", "ALTER USER", "DROP USER", "GRANT ANY PRIVILEGE", "GRANT ANY ROLE", "GRANT ANY OBJECT PRIVILEGE", "BECOME USER", "CREATE ANY TABLE", "ALTER ANY TABLE", "DROP ANY TABLE", "INSERT ANY TABLE", "UPDATE ANY TABLE", "DELETE ANY TABLE", "CREATE ANY PROCEDURE", "ALTER ANY PROCEDURE", "DROP ANY PROCEDURE", "EXECUTE ANY PROCEDURE", "SELECT ANY DICTIONARY", "SELECT ANY TABLE", "CREATE ANY DIRECTORY", "DROP ANY DIRECTORY", "CREATE ANY JOB", "CREATE EXTERNAL JOB", "ADMINISTER DATABASE TRIGGER", "MANAGE SCHEDULER", "FLASHBACK ANY TABLE"}
    if not permitted_roles.issubset(roles):
        raise ValueError("This workbench requires the CONNECT and RESOURCE roles. Have a database administrator provision the restricted schema account.")
    if privileges & elevated_privileges:
        raise ValueError("This Oracle account has direct or elevated system privileges and cannot be used with the workbench.")
    return {"connected": True, "user": user, "loginUser": str(requested_user or "").strip(), "service": service,
            "defaultSchemaOnly": is_default_connection(connect_string, user or requested_user)}


def workbench_home(request):
    return render(request, "index.html")


def frontend_asset(request, asset):
    if asset not in _STATIC_FILES: return HttpResponse(status=404)
    target = ROOT / asset
    if not target.is_file(): return HttpResponse(status=404)
    response = FileResponse(target.open("rb"))
    response["Cache-Control"] = "public, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@csrf_exempt
@require_http_methods(["POST"])
def connect_oracle(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    key = request.META.get("REMOTE_ADDR", "unknown")
    now = time.time()
    attempts = [stamp for stamp in _connect_attempts.get(key, []) if now - stamp < 60]
    if len(attempts) >= 10:
        _connect_attempts[key] = attempts
        return json_error("Too many connection attempts. Wait a minute and try again.", 429)
    attempts.append(now); _connect_attempts[key] = attempts
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 413 if str(exc) == "Request is too large." else 400)
    user, password, connect_string = payload.get("user"), payload.get("password"), payload.get("connectString")
    if not all(isinstance(value, str) and value.strip() for value in (user, connect_string)) or not isinstance(password, str) or not password:
        return json_error("Enter the Oracle username, password, and connect string.")
    with _lock:
        if len(_oracle_sessions) >= 100 and not current_oracle_session(request, touch=False):
            return json_error("The server has reached its active login limit.", 503)
    connection = None
    try:
        options = connect_options({"user": user.strip(), "password": password, "connectString": connect_string.strip(), "admin": payload.get("admin")})
        connection = open_oracle_connection(options)
        result = connect_info(connection, connect_string.strip(), user.strip())
        old_session_key = request.session.session_key
        prior = current_oracle_session(request, touch=False)
        if prior:
            for connection_name in ("sqlplus", "editor"):
                if prior.get(connection_name):
                    try: prior[connection_name].close()
                    except Exception: pass
        request.session.cycle_key()
        with _lock:
            if old_session_key and old_session_key != request.session.session_key:
                _oracle_sessions.pop(old_session_key, None)
        workspace_id = request.COOKIES.get("ora_workspace") or secrets.token_urlsafe(32)
        request.session["oracle_connected"] = True
        session_key = request.session.session_key
        with _lock:
            _oracle_sessions[session_key] = {"settings": {"user": user.strip(), "password": password, "connectString": connect_string.strip()}, "workspace_id": workspace_id, "sqlplus": connection, "editor": None, "status": result, "last_activity": now}
        connection = None
        response = JsonResponse(result)
        response.set_cookie("ora_workspace", workspace_id, max_age=31536000, httponly=True, samesite="Strict", secure=request.is_secure())
        return response
    except Exception as exc: return json_error(getattr(exc, "message", str(exc)))
    finally:
        if connection:
            try: connection.close()
            except Exception: pass


@csrf_exempt
@require_http_methods(["POST"])
def disconnect_oracle(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    session = current_oracle_session(request, touch=False)
    if session:
        for connection_name in ("sqlplus", "editor"):
            if session.get(connection_name):
                try: session[connection_name].close()
                except Exception: pass
    with _lock:
        if request.session.session_key: _oracle_sessions.pop(request.session.session_key, None)
    request.session.flush()
    response = JsonResponse({"connected": False})
    response.delete_cookie("ora_session", samesite="Strict")
    response.delete_cookie("ora_workspace", samesite="Strict")
    return response


@require_http_methods(["GET"])
def oracle_status(request):
    session = current_oracle_session(request)
    if not session: return JsonResponse({"connected": False})
    # The browser polls this endpoint to refresh its UI. Reopening an Oracle
    # connection for every poll adds a network round trip without improving
    # session validity; execution requests report real connection failures.
    return JsonResponse(session.get("status", {
        "connected": True,
        "user": session["settings"]["user"],
        "loginUser": session["settings"]["user"],
    }))


def execute_one(connection, sql, binds, auto_commit):
    connection.autocommit = auto_commit
    with connection.cursor() as cursor:
        is_plsql = bool(re.match(r"^(?:DECLARE|BEGIN|CREATE(?:\s+OR\s+REPLACE)?\s+(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE))\b", sql, re.I))
        if is_plsql:
            with connection.cursor() as enable_cursor: enable_cursor.execute("BEGIN DBMS_OUTPUT.ENABLE(NULL); END;")
        cursor.execute(sql, driver_binds(sql, binds))
        description = cursor.description or []
        columns = [{"name": item[0], "type": getattr(item[1], "name", "")} for item in description]
        rows = [[json_value(value) for value in row] for row in cursor.fetchmany(MAX_ROWS)] if columns else []
        rows_affected = cursor.rowcount if cursor.rowcount >= 0 and not columns else None
        output_lines = []
        if is_plsql:
            line_var = cursor.var(str, 32767); status_var = cursor.var(int)
            while True:
                cursor.execute("BEGIN DBMS_OUTPUT.GET_LINE(:line, :status); END;", line=line_var, status=status_var)
                if status_var.getvalue() != 0: break
                output_lines.append(line_var.getvalue())
        compilation_errors = []
        create_match = re.match(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(MATERIALIZED\s+VIEW|PACKAGE\s+BODY|TYPE\s+BODY|PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE|VIEW)\s+(\"(?:\"\"|[^\"])+\"|[\w$#]+)", sql, re.I)
        if create_match:
            object_type = re.sub(r"\s+", " ", create_match.group(1).upper())
            raw_name = create_match.group(2)
            object_name = raw_name[1:-1].replace('""', '"') if raw_name.startswith('"') else raw_name.upper()
            with connection.cursor() as error_cursor:
                error_cursor.execute(
                    "SELECT line, position, text FROM user_errors WHERE name=:name AND type=:type AND attribute='ERROR' ORDER BY sequence",
                    {"name": object_name, "type": object_type},
                )
                compilation_errors = [
                    {"line": line, "column": position, "message": text}
                    for line, position, text in error_cursor.fetchall()
                ]
        return columns, rows, rows_affected, output_lines, compilation_errors


@csrf_exempt
@require_http_methods(["POST"])
def execute_sql(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 413 if str(exc) == "Request is too large." else 400)
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip(): return json_error("Provide a SQL statement.")
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    started = time.monotonic(); connection = None; failing = ""
    statements = split_script(sql)
    statements = [item for item in statements if item]
    if not statements: return json_error("Provide a SQL statement.")
    for statement in statements:
        blocked = blocked_admin_command(statement)
        if blocked: return json_error(f"{blocked} is disabled by the workbench security policy.", 403)
    connection_key = "sqlplus" if payload.get("sqlplus") else "editor"
    try:
        connection = session.get(connection_key)
        if connection:
            try:
                with connection.cursor() as cursor: cursor.execute("SELECT 1 FROM dual")
            except Exception:
                try: connection.close()
                except Exception: pass
                connection = None
        if not connection:
            connection = open_oracle_connection(connect_options(session["settings"]))
            session[connection_key] = connection
        columns, rows, affected, output, messages = [], [], None, [], []
        for raw in statements:
            statement = re.sub(r"(?:;\s*|/\s*)+$", "", raw.strip()).strip()
            if not statement: continue
            failing = statement
            result_columns, result_rows, rows_affected, output_lines, compilation_errors = execute_one(connection, statement, payload.get("binds"), bool(payload.get("autoCommit")))
            if result_columns: columns, rows = result_columns, result_rows
            if rows_affected is not None: affected = rows_affected
            output.extend(output_lines)
            if compilation_errors:
                error_text = "\n".join(f"{item['message']} (line {item['line']}, column {item['column']})" for item in compilation_errors)
                if connection_key == "editor":
                    try: connection.rollback()
                    except Exception: pass
                return json_error("Oracle compilation failed:\n" + error_text, 400, compilationErrors=compilation_errors, statement=statement)
            messages.append(f"Query completed ({len(result_rows)} rows)" if result_columns else f"Statement {len(messages)+1} executed")
        return JsonResponse({"columns": columns, "rows": rows, "outputLines": output, "rowsAffected": affected,
            "truncated": bool(columns and len(rows) == MAX_ROWS), "maxRows": MAX_ROWS,
            "durationMs": round((time.monotonic()-started)*1000), "message": " · ".join(messages), "statementsExecuted": len(messages)})
    except Exception as exc:
        message = getattr(exc, "message", str(exc)); code = getattr(exc, "code", None); offset = getattr(exc, "offset", None)
        broken = bool(re.search(r"\b(?:NJS-(?:003|500|511|521)|ORA-(?:03113|03114|03135|12537|12547|12570|12571))\b", str(message)))
        if broken and connection:
            try: connection.close()
            except Exception: pass
            session[connection_key] = None
        elif connection and connection_key == "editor":
            try: connection.rollback()
            except Exception: pass
        return json_error(message, 400, code=code, offset=offset, statement=failing or None)


@require_http_methods(["GET"])
def list_objects(request):
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    settings_data = session["settings"]; connection = None
    default_only = is_default_connection(settings_data["connectString"], settings_data["user"])
    excluded = "('SYS','OUTLN','DBSNMP','APPQOSSYS','AUDSYS','CTXSYS','DBSFWUSER','GSMADMIN_INTERNAL','LBACSYS','MDSYS','OJVMSYS','ORDDATA','ORDSYS','WMSYS','XDB')"
    owner_filter = "owner = USER" if default_only else f"owner NOT IN {excluded}"
    try:
        connection = open_oracle_connection(connect_options(settings_data))
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT owner, object_name, object_type, status, created FROM all_objects WHERE object_type IN ('TABLE','VIEW','MATERIALIZED VIEW','INDEX','SEQUENCE','PROCEDURE','FUNCTION','PACKAGE','TRIGGER','TYPE','SYNONYM') AND {owner_filter} ORDER BY CASE WHEN owner = USER THEN 0 ELSE 1 END, owner, object_type, object_name FETCH FIRST 4000 ROWS ONLY")
            keys = [column[0] for column in cursor.description]; objects = [dict(zip(keys, row)) for row in cursor.fetchall()]
            cursor.execute(f"SELECT owner, constraint_name AS object_name, 'CONSTRAINT' AS object_type, status, table_name FROM all_constraints WHERE {owner_filter} ORDER BY CASE WHEN owner = USER THEN 0 ELSE 1 END, owner, table_name, constraint_name FETCH FIRST 10000 ROWS ONLY")
            keys = [column[0] for column in cursor.description]; objects.extend(dict(zip(keys, row)) for row in cursor.fetchall())
        return JsonResponse({"objects": [{key: json_value(value) for key, value in item.items()} for item in objects], "user": settings_data["user"].upper(), "defaultSchemaOnly": default_only})
    except Exception as exc: return json_error(getattr(exc, "message", str(exc)))
    finally:
        if connection:
            try: connection.close()
            except Exception: pass


@require_http_methods(["GET"])
def object_details(request, owner, name):
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    owner, name = owner.upper(), name.upper(); view = request.GET.get("view", "structure")
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", owner) or not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", name): return json_error("Object name is not a supported Oracle identifier.")
    data = session["settings"]
    if is_default_connection(data["connectString"], data["user"]) and owner != data["user"].upper(): return json_error("Navigator can access objects in your connected schema only.", 403)
    connection = None
    try:
        connection = open_oracle_connection(connect_options(data))
        with connection.cursor() as cursor:
            if view == "data":
                cursor.execute("SELECT object_type FROM all_objects WHERE owner=:owner AND object_name=:name AND object_type IN ('TABLE','VIEW','MATERIALIZED VIEW') AND ROWNUM=1", owner=owner, name=name)
                if not cursor.fetchone(): return json_error("Data preview is available for tables and views only.")
                cursor.execute(f'SELECT * FROM "{owner}"."{name}" FETCH FIRST 100 ROWS ONLY')
                cols = [item[0] for item in cursor.description or []]; rows = [[json_value(value) for value in row] for row in cursor.fetchall()]
                return JsonResponse({"owner": owner, "name": name, "view": view, "columns": [{"name": col} for col in cols], "rows": rows, "truncated": len(rows) == 100})
            if view == "constraints":
                cursor.execute("SELECT c.constraint_name, c.constraint_type, c.status, c.validated, cc.column_name, cc.position, c.r_constraint_name FROM all_constraints c LEFT JOIN all_cons_columns cc ON cc.owner=c.owner AND cc.constraint_name=c.constraint_name AND cc.table_name=c.table_name WHERE c.owner=:owner AND c.table_name=:name ORDER BY c.constraint_name, cc.position", owner=owner, name=name)
                keys = [item[0] for item in cursor.description or []]
                return JsonResponse({"owner": owner, "name": name, "view": view, "constraints": [dict(zip(keys, row)) for row in cursor.fetchall()]})
            if view == "structure":
                cursor.execute("SELECT object_type FROM all_objects WHERE owner=:owner AND object_name=:name AND object_type='INDEX' AND ROWNUM=1", owner=owner, name=name)
                if cursor.fetchone():
                    cursor.execute("SELECT c.column_name AS COLUMN_NAME, c.column_position AS COLUMN_ID, i.uniqueness AS DATA_TYPE, NULL AS NULLABLE FROM all_ind_columns c JOIN all_indexes i ON i.owner=c.index_owner AND i.index_name=c.index_name WHERE c.index_owner=:owner AND c.index_name=:name ORDER BY c.column_position", owner=owner, name=name)
                    keys = [item[0] for item in cursor.description or []]
                    return JsonResponse({"owner": owner, "name": name, "view": view, "columns": [dict(zip(keys, row)) for row in cursor.fetchall()], "objectType": "INDEX"})
                cursor.execute("SELECT column_name, data_type, nullable, data_length, char_length, char_used, data_precision, data_scale FROM all_tab_columns WHERE owner=:owner AND table_name=:name ORDER BY column_id", owner=owner, name=name)
                keys = [item[0] for item in cursor.description or []]
                return JsonResponse({"owner": owner, "name": name, "view": view, "columns": [dict(zip(keys, row)) for row in cursor.fetchall()]})
        return json_error("Unknown object property view.")
    except Exception as exc: return json_error(getattr(exc, "message", str(exc)))
    finally:
        if connection:
            try: connection.close()
            except Exception: pass


@csrf_exempt
@require_http_methods(["GET", "POST"])
def sql_files(request):
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    folder = sql_folder(session)
    if request.method == "GET":
        try:
            folder.mkdir(parents=True, exist_ok=True)
            files = sorted((item.name for item in folder.iterdir() if item.is_file() and item.suffix.lower() == ".sql"), key=str.casefold)
            return JsonResponse({"files": files})
        except Exception as exc: return json_error(getattr(exc, "strerror", str(exc)), 500)
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 413 if str(exc) == "Request is too large." else 400)
    if not isinstance(payload.get("sql"), str): return json_error("SQL text is required.")
    if len(payload["sql"].encode("utf-8")) > MAX_BODY: return json_error("File is too large.", 413)
    try:
        filename = safe_sql_filename(payload.get("fileName")); folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(payload["sql"], encoding="utf-8")
        return JsonResponse({"fileName": filename})
    except Exception as exc: return json_error(exc)


@require_http_methods(["GET"])
def sql_file(request, name):
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    try:
        filename = safe_sql_filename(name)
        sql = (sql_folder(session) / filename).read_text(encoding="utf-8")
        return JsonResponse({"fileName": filename, "sql": sql})
    except Exception: return json_error("Saved SQL file not found.", 404)
