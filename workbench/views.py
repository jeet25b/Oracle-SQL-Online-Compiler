import base64
import hashlib
import json
import logging
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
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import FileResponse, JsonResponse, HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from .models import Announcement, Assignment, AttendanceSession, AuditEvent, ClassSection, Course, Enrollment, PortalUser, Submission, TeacherAssignment, StudentFee, StudentFeePayment
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

ROOT = Path(settings.BASE_DIR)
SQL_FILES = ROOT / "sql-files"
MAX_BODY = 256 * 1024
MAX_ROWS = 500
IDLE_SECONDS = 30 * 60
_oracle_sessions = {}
_connect_attempts = {}
_lock = RLock()
_STATIC_FILES = {"index.html", "app.js", "styles.css", "output.css", "lms.html", "lms.css"}


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
            if old and old.get("sqlplus"):
                try: old["sqlplus"].close()
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
    options = {"user": data["user"], "password": data["password"], "dsn": data["connectString"]}
    if data.get("admin") is True: options["mode"] = oracledb.AUTH_MODE_SYSDBA
    return options


def connect_info(connection, connect_string, requested_user):
    with connection.cursor() as cursor:
        cursor.execute("SELECT SYS_CONTEXT('USERENV','SERVICE_NAME'), USER FROM dual")
        service, user = cursor.fetchone()
    return {"connected": True, "user": user, "loginUser": str(requested_user or "").strip(), "service": service,
            "defaultSchemaOnly": is_default_connection(connect_string, user or requested_user)}


def workbench_home(request):
    return render(request, "index.html")


def learning_portal(request):
    return render(request, "lms.html")


def frontend_asset(request, asset):
    if asset not in _STATIC_FILES: return HttpResponse(status=404)
    target = ROOT / asset
    if not target.is_file(): return HttpResponse(status=404)
    response = FileResponse(target.open("rb"))
    response["Cache-Control"] = "no-cache"
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
        connection = oracledb.connect(**options)
        result = connect_info(connection, connect_string.strip(), user.strip())
        old_session_key = request.session.session_key
        prior = current_oracle_session(request, touch=False)
        if prior and prior.get("sqlplus"):
            try: prior["sqlplus"].close()
            except Exception: pass
        request.session.cycle_key()
        with _lock:
            if old_session_key and old_session_key != request.session.session_key:
                _oracle_sessions.pop(old_session_key, None)
        workspace_id = request.COOKIES.get("ora_workspace") or secrets.token_urlsafe(32)
        request.session["oracle_connected"] = True
        session_key = request.session.session_key
        with _lock:
            _oracle_sessions[session_key] = {"settings": {"user": user.strip(), "password": password, "connectString": connect_string.strip(), "admin": payload.get("admin") is True}, "workspace_id": workspace_id, "sqlplus": connection, "last_activity": now}
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
    if session and session.get("sqlplus"):
        try: session["sqlplus"].close()
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
    connection = None
    try:
        connection = oracledb.connect(**connect_options(session["settings"]))
        return JsonResponse(connect_info(connection, session["settings"]["connectString"], session["settings"]["user"]))
    except Exception as exc: return JsonResponse({"connected": False, "error": getattr(exc, "message", str(exc))})
    finally:
        if connection:
            try: connection.close()
            except Exception: pass


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
        return columns, rows, rows_affected, output_lines


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
    started = time.monotonic(); connection = None; own_connection = not bool(payload.get("sqlplus")); failing = ""
    try:
        if own_connection:
            connection = oracledb.connect(**connect_options(session["settings"]))
        else:
            connection = session.get("sqlplus")
            try:
                with connection.cursor() as cursor: cursor.execute("SELECT 1 FROM dual")
            except Exception:
                try: connection.close()
                except Exception: pass
                connection = oracledb.connect(**connect_options(session["settings"]))
                session["sqlplus"] = connection
        statements = split_script(sql) if payload.get("mode") == "script" else [re.sub(r"(?:;\s*|/\s*)+$", "", sql.strip()).strip()]
        statements = [item for item in statements if item]
        if not statements: return json_error("Provide a SQL statement.")
        columns, rows, affected, output, messages = [], [], None, [], []
        for raw in statements:
            statement = re.sub(r"(?:;\s*|/\s*)+$", "", raw.strip()).strip()
            if not statement: continue
            failing = statement
            result_columns, result_rows, rows_affected, output_lines = execute_one(connection, statement, payload.get("binds"), bool(payload.get("autoCommit")) if payload.get("sqlplus") else True)
            if result_columns: columns, rows = result_columns, result_rows
            if rows_affected is not None: affected = rows_affected
            output.extend(output_lines)
            messages.append(f"Query completed ({len(result_rows)} rows)" if result_columns else f"Statement {len(messages)+1} executed")
        return JsonResponse({"columns": columns, "rows": rows, "outputLines": output, "rowsAffected": affected,
            "truncated": bool(columns and len(rows) == MAX_ROWS), "maxRows": MAX_ROWS,
            "durationMs": round((time.monotonic()-started)*1000), "message": " · ".join(messages), "statementsExecuted": len(messages)})
    except Exception as exc:
        message = getattr(exc, "message", str(exc)); code = getattr(exc, "code", None); offset = getattr(exc, "offset", None)
        broken = bool(re.search(r"\b(?:NJS-(?:003|500|511|521)|ORA-(?:03113|03114|03135|12537|12547|12570|12571))\b", str(message)))
        if payload.get("sqlplus") and broken and connection:
            try: connection.close()
            except Exception: pass
            session["sqlplus"] = None
        return json_error(message, 400, code=code, offset=offset, statement=failing or None)
    finally:
        if own_connection and connection:
            try: connection.close()
            except Exception: pass


@require_http_methods(["GET"])
def list_objects(request):
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    settings_data = session["settings"]; connection = None
    default_only = is_default_connection(settings_data["connectString"], settings_data["user"])
    excluded = "('SYS','OUTLN','DBSNMP','APPQOSSYS','AUDSYS','CTXSYS','DBSFWUSER','GSMADMIN_INTERNAL','LBACSYS','MDSYS','OJVMSYS','ORDDATA','ORDSYS','WMSYS','XDB')"
    owner_filter = "owner = USER" if default_only else f"owner NOT IN {excluded}"
    try:
        connection = oracledb.connect(**connect_options(settings_data))
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
        connection = oracledb.connect(**connect_options(data))
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
                cursor.execute("SELECT column_name, data_type, nullable FROM all_tab_columns WHERE owner=:owner AND table_name=:name ORDER BY column_id", owner=owner, name=name)
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


def portal_human_challenge(request, key="portal_login_human_answer"):
    left = secrets.randbelow(8) + 2
    right = secrets.randbelow(8) + 2
    request.session[key] = str(left + right)
    return {"human_question": f"What is {left} + {right}?"}


def portal_csrf_failure(request, reason=""):
    logging.getLogger("django.security.csrf").warning(
        "Portal CSRF rejection: reason=%s host=%s origin=%s referer=%s cookie_present=%s token_present=%s",
        reason,
        request.get_host(),
        request.headers.get("Origin", "-"),
        request.headers.get("Referer", "-"),
        settings.CSRF_COOKIE_NAME in request.COOKIES,
        bool(request.POST.get("csrfmiddlewaretoken")),
    )
    return HttpResponseForbidden("CSRF verification failed. Reload the login page and try again.")


def portal_login(request, student_only=False):
    if request.user.is_authenticated: return redirect("portal-dashboard")
    error = ""
    if request.method == "POST":
        expected_answer = request.session.pop("portal_login_human_answer", None)
        submitted_answer = request.POST.get("human_answer", "").strip()
        if expected_answer is None or not submitted_answer.isdecimal() or submitted_answer != expected_answer:
            error = "Please solve the human verification question correctly."
        else:
            username = request.POST.get("username", "").strip()
            password = request.POST.get("password", "")
            user = authenticate(request, username=username, password=password)
            if user and user.is_active:
                if student_only and user.role != PortalUser.Role.STUDENT:
                    error = "This sign-in page is for student accounts only."
                else:
                    prior_session_key = request.session.session_key
                    login(request, user)
                    if prior_session_key and request.session.session_key != prior_session_key:
                        with _lock:
                            oracle_state = _oracle_sessions.pop(prior_session_key, None)
                            if oracle_state: _oracle_sessions[request.session.session_key] = oracle_state
                    attendance = AttendanceSession.objects.create(user=user)
                    request.session["attendance_session_id"] = attendance.pk
                    AuditEvent.objects.create(actor=user, action="login", target=user.username)
                    return redirect("portal-dashboard")
            elif student_only and PortalUser.objects.filter(username__iexact=username, role=PortalUser.Role.STUDENT, registration_status__in=[PortalUser.RegistrationStatus.PENDING_ADMIN, PortalUser.RegistrationStatus.PENDING_ACCOUNTANT]).exists():
                pending = PortalUser.objects.get(username__iexact=username)
                error = "Your registration is awaiting administrator review." if pending.registration_status == PortalUser.RegistrationStatus.PENDING_ADMIN else "Your registration is awaiting accountant approval."
            else:
                error = "The username or password was not recognized."
    challenge = portal_human_challenge(request)
    template = "portal_student_login.html" if student_only else "portal_login.html"
    response = render(request, template, {"error": error, "student_only": student_only, **challenge})
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


def student_login(request):
    return portal_login(request, student_only=True)


def student_registration_pending(request):
    return render(request, "portal_student_registration_pending.html")


def student_register(request):
    if request.user.is_authenticated:
        return redirect("portal-dashboard")
    errors = []
    values = {key: request.POST.get(key, "").strip() for key in ("full_name", "email", "username")} if request.method == "POST" else {}
    if request.method == "POST":
        expected_answer = request.session.pop("portal_registration_human_answer", None)
        human_answer = request.POST.get("human_answer", "").strip()
        if expected_answer is None or not human_answer.isdecimal() or human_answer != expected_answer:
            errors.append("Please solve the human verification question correctly.")

        full_name = values.get("full_name", "")
        email = values.get("email", "")
        username = values.get("username", "")
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")
        student = PortalUser(username=username, email=email, role=PortalUser.Role.STUDENT)
        student.first_name, _, student.last_name = full_name.partition(" ")

        if not full_name:
            errors.append("Enter your full name.")
        elif len(student.first_name) > 150 or len(student.last_name) > 150:
            errors.append("Keep each part of your name to 150 characters or fewer.")
        if not username:
            errors.append("Choose a username.")
        else:
            try:
                PortalUser._meta.get_field("username").clean(username, student)
            except ValidationError as exc:
                errors.extend(exc.messages)
            if PortalUser.objects.filter(username__iexact=username).exists():
                errors.append("That username is already in use.")
        if not email:
            errors.append("Enter your email address.")
        elif len(email) > 254:
            errors.append("Email addresses must be 254 characters or fewer.")
        else:
            try:
                validate_email(email)
            except ValidationError:
                errors.append("Enter a valid email address.")
            if PortalUser.objects.filter(email__iexact=email).exists():
                errors.append("That email address is already registered.")
        if password != confirm_password:
            errors.append("The passwords do not match.")
        if password:
            try:
                password_validation.validate_password(password, student)
            except ValidationError as exc:
                errors.extend(exc.messages)
        else:
            errors.append("Choose a password.")
        if not errors:
            student.registration_status = PortalUser.RegistrationStatus.PENDING_ADMIN
            student.is_active = False
            student.set_password(password)
            student.save()
            AuditEvent.objects.create(actor=None, action="student_registration", target=student.username)
            return redirect("student-registration-pending")

    challenge = portal_human_challenge(request, "portal_registration_human_answer")
    response = render(request, "portal_student_register.html", {"errors": errors, "values": values, **challenge})
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@login_required
@require_http_methods(["POST"])
def portal_logout(request):
    session_key = request.session.session_key
    oracle_state = current_oracle_session(request, touch=False)
    if oracle_state and oracle_state.get("sqlplus"):
        try: oracle_state["sqlplus"].close()
        except Exception: pass
    with _lock:
        if session_key: _oracle_sessions.pop(session_key, None)
    attendance_id = request.session.get("attendance_session_id")
    if attendance_id:
        AttendanceSession.objects.filter(pk=attendance_id, user=request.user, logout_at__isnull=True).update(logout_at=timezone.now())
    AuditEvent.objects.create(actor=request.user, action="logout", target=request.user.username)
    logout(request)
    return redirect("portal-login")


@login_required
def portal_dashboard(request):
    user = request.user
    if user.role == PortalUser.Role.ADMIN or user.is_superuser:
        return redirect("portal-admin-dashboard")
    context = {"role": user.role, "user": user}
    if user.role == PortalUser.Role.TEACHER:
        assignments = TeacherAssignment.objects.filter(teacher=user).select_related("class_section", "class_section__course")
        context["teaching_assignments"] = assignments
        context["submissions"] = Submission.objects.filter(assignment__created_by=user).select_related("assignment", "student").order_by("-submitted_at")[:10]
        context["pending_submissions"] = Submission.objects.filter(assignment__created_by=user, score__isnull=True).count()
    elif user.role == PortalUser.Role.ACCOUNTANT:
        return redirect("portal-accounts")
    else:
        context["enrollments"] = Enrollment.objects.filter(student=user, active=True).select_related("class_section", "class_section__course")
        context["assignments"] = Assignment.objects.filter(class_section__enrollments__student=user, published=True).order_by("due_at")[:10]
        context["announcements"] = Announcement.objects.filter(published=True).order_by("-created_at")[:8]
    return render(request, "portal_dashboard.html", context)


@login_required
@user_passes_test(lambda user: user.is_active and (user.role == PortalUser.Role.ADMIN or user.is_superuser))
def portal_admin_dashboard(request):
    today = timezone.localdate()
    context = {
        "role": PortalUser.Role.ADMIN,
        "user": request.user,
        "today": today,
        "counts": {
            "users": PortalUser.objects.count(),
            "teachers": PortalUser.objects.filter(role=PortalUser.Role.TEACHER).count(),
            "students": PortalUser.objects.filter(role=PortalUser.Role.STUDENT).count(),
            "pending_students": PortalUser.objects.filter(role=PortalUser.Role.STUDENT, registration_status=PortalUser.RegistrationStatus.PENDING_ADMIN).count(),
            "courses": Course.objects.count(),
            "classes": ClassSection.objects.count(),
            "attendance_today": AttendanceSession.objects.filter(login_at__date=today).count(),
        },
        "active_sessions": AttendanceSession.objects.filter(logout_at__isnull=True).select_related("user").order_by("-login_at")[:8],
        "recent_activity": AuditEvent.objects.select_related("actor").order_by("-created_at")[:10],
        "recent_announcements": Announcement.objects.select_related("created_by").order_by("-created_at")[:5],
    }
    return render(request, "portal_admin_dashboard.html", context)


@login_required
@user_passes_test(lambda user: user.is_active and user.role in (PortalUser.Role.ADMIN, PortalUser.Role.ACCOUNTANT))
@require_http_methods(["GET", "POST"])
def portal_accounts(request):
    is_accountant = request.user.role == PortalUser.Role.ACCOUNTANT
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in {"approve_activation", "reject_activation"} and not is_accountant:
            return HttpResponseForbidden("Only an accountant can complete registration activation approval.")
        student_id = request.POST.get("student_id")
        try:
            student = PortalUser.objects.get(pk=student_id, role=PortalUser.Role.STUDENT)
        except (PortalUser.DoesNotExist, ValueError, TypeError):
            return HttpResponseForbidden("Student account not found.")

        if action in {"approve_activation", "reject_activation"}:
            if student.registration_status != PortalUser.RegistrationStatus.PENDING_ACCOUNTANT:
                return HttpResponseForbidden("This registration is not awaiting accountant approval.")
            student.registration_status = (PortalUser.RegistrationStatus.APPROVED if action == "approve_activation"
                                           else PortalUser.RegistrationStatus.REJECTED)
            student.is_active = action == "approve_activation"
            student.deactivation_source = ""
            student.save(update_fields=("registration_status", "is_active", "deactivation_source"))
            AuditEvent.objects.create(actor=request.user, action=f"student_{action}", target=student.username)
        elif action == "deactivate_student":
            student.is_active = False
            student.deactivation_source = (PortalUser.DeactivationSource.ACCOUNTANT if is_accountant
                                           else PortalUser.DeactivationSource.ADMIN)
            student.save(update_fields=("is_active", "deactivation_source"))
            AuditEvent.objects.create(actor=request.user, action="student_deactivated_accountant" if is_accountant
                                      else "student_deactivated_admin", target=student.username)
        elif action == "activate_student":
            if student.deactivation_source == PortalUser.DeactivationSource.ACCOUNTANT and not is_accountant:
                return HttpResponseForbidden("Only an accountant can reactivate a student deactivated by the account team.")
            if is_accountant and student.deactivation_source != PortalUser.DeactivationSource.ACCOUNTANT:
                return HttpResponseForbidden("Accountants can reactivate only students deactivated by the account team.")
            if student.registration_status != PortalUser.RegistrationStatus.APPROVED:
                return HttpResponseForbidden("Only an approved student can be activated here.")
            student.is_active = True
            student.deactivation_source = ""
            student.save(update_fields=("is_active", "deactivation_source"))
            AuditEvent.objects.create(actor=request.user, action="student_activated", target=student.username)
        elif action == "add_fee":
            if student.registration_status != PortalUser.RegistrationStatus.APPROVED:
                return HttpResponseForbidden("Fees can only be assigned to approved students.")
            try:
                amount = Decimal(request.POST.get("amount", ""))
                if not amount.is_finite() or amount <= 0:
                    raise ValueError
                due_date = request.POST.get("due_date") or None
                if due_date:
                    date.fromisoformat(due_date)
                fee = StudentFee.objects.create(student=student, title=request.POST.get("title", "").strip()[:160],
                    academic_year=request.POST.get("academic_year", "").strip()[:20], amount_due=amount,
                    due_date=due_date, created_by=request.user)
                if not fee.title:
                    fee.delete()
                    return HttpResponseForbidden("Enter a fee description.")
            except (ValueError, TypeError, ArithmeticError):
                return HttpResponseForbidden("Enter a valid fee amount and due date.")
            AuditEvent.objects.create(actor=request.user, action="student_fee_created", target=student.username,
                                      details={"fee": fee.title, "amount": str(fee.amount_due)})
        elif action == "record_payment":
            try:
                fee = StudentFee.objects.get(pk=request.POST.get("fee_id"), student=student)
                amount = Decimal(request.POST.get("payment_amount", ""))
                if not amount.is_finite() or amount <= 0 or amount > fee.balance_due:
                    raise ValueError
                StudentFeePayment.objects.create(fee=fee, amount=amount,
                    method=request.POST.get("method") if request.POST.get("method") in StudentFeePayment.Method.values else StudentFeePayment.Method.OTHER,
                    reference=request.POST.get("reference", "").strip()[:100], received_by=request.user)
            except (StudentFee.DoesNotExist, ValueError, TypeError, ArithmeticError):
                return HttpResponseForbidden("Enter a valid payment amount no greater than the outstanding balance.")
            AuditEvent.objects.create(actor=request.user, action="student_fee_payment_recorded", target=student.username,
                                      details={"fee_id": fee.pk, "amount": str(amount)})
        else:
            return HttpResponseForbidden("Unknown account action.")
        return redirect("portal-accounts")

    context = {
        "user": request.user,
        "is_accountant": is_accountant,
        "pending_activations": PortalUser.objects.filter(role=PortalUser.Role.STUDENT, registration_status=PortalUser.RegistrationStatus.PENDING_ACCOUNTANT).order_by("date_joined"),
        "students": PortalUser.objects.filter(role=PortalUser.Role.STUDENT, registration_status=PortalUser.RegistrationStatus.APPROVED).order_by("last_name", "first_name", "username"),
        "fees": StudentFee.objects.select_related("student", "created_by").prefetch_related("payments").order_by("-created_at")[:100],
    }
    return render(request, "portal_accounts_dashboard.html", context)


@login_required
@user_passes_test(lambda user: user.is_active and user.role in (PortalUser.Role.TEACHER, PortalUser.Role.ADMIN))
@require_http_methods(["GET", "POST"])
def portal_teacher_students(request):
    teacher = request.user.role == PortalUser.Role.TEACHER
    assigned_ids = TeacherAssignment.objects.filter(teacher=request.user).values_list("class_section_id", flat=True)
    scoped = PortalUser.objects.filter(role=PortalUser.Role.STUDENT, enrollments__active=True)
    if teacher:
        scoped = scoped.filter(enrollments__class_section_id__in=assigned_ids)
    scoped = scoped.distinct()
    if request.method == "POST":
        try:
            student = scoped.get(pk=request.POST.get("student_id"))
        except (PortalUser.DoesNotExist, ValueError, TypeError):
            return HttpResponseForbidden("You cannot manage this student.")
        action = request.POST.get("action")
        if action == "deactivate_student":
            student.is_active = False
            student.deactivation_source = PortalUser.DeactivationSource.TEACHER
            student.save(update_fields=("is_active", "deactivation_source"))
        elif action == "activate_student":
            if student.deactivation_source == PortalUser.DeactivationSource.ACCOUNTANT:
                return HttpResponseForbidden("Only an accountant can reactivate a student deactivated by the account team.")
            if student.registration_status != PortalUser.RegistrationStatus.APPROVED:
                return HttpResponseForbidden("Only an approved student can be activated.")
            student.is_active = True
            student.deactivation_source = ""
            student.save(update_fields=("is_active", "deactivation_source"))
        else:
            return HttpResponseForbidden("Unknown student action.")
        AuditEvent.objects.create(actor=request.user, action=f"student_{action}_teacher", target=student.username)
        return redirect("portal-teacher-students")
    return render(request, "portal_teacher_students.html", {"students": scoped.order_by("last_name", "first_name", "username"), "user": request.user})


@csrf_exempt
@require_http_methods(["POST"])
def record_activity(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to record activity.", 401)
    attendance_id = request.session.get("attendance_session_id")
    if attendance_id:
        AttendanceSession.objects.filter(pk=attendance_id, user=request.user, logout_at__isnull=True).update(last_activity_at=timezone.now())
    return JsonResponse({"recorded": True})
