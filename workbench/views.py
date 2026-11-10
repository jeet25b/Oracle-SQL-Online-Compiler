import base64
from datetime import timedelta
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock
from weakref import WeakValueDictionary

import oracledb
from .password_privacy import redact_text
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Subquery
from django.db import transaction
from django.http import FileResponse, JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

ROOT = Path(settings.BASE_DIR)
SQL_FILES = ROOT / "sql-files"
MAX_BODY = 256 * 1024
MAX_ROWS = 500
ORACLE_CALL_TIMEOUT_MS = 5000
ORACLE_METADATA_TIMEOUT_MS = 30000
IDLE_SECONDS = 30 * 60
_oracle_sessions = {}
_oracle_session_tokens = {}
_connect_attempts = {}
_saved_connection_locks = WeakValueDictionary()
_lock = RLock()
_sqlplus_requests = {}


def _validated_input_source_map(value, start=0, end=MAX_BODY):
    if not isinstance(value, list): return []
    output = []
    for item in value[:20000]:
        if not isinstance(item, (list, tuple)) or len(item) != 3: continue
        left, right, source = item
        if type(left) is not int or type(right) is not int or source not in {"typed", "pasted"}: continue
        left, right = max(left, start), min(right, end)
        if right > left: output.append([left - start, right - start, source])
    return output


def _validated_pasted_texts(value):
    # Clipboard contents may contain an unlabelled password. Keep metrics only.
    return []


def _execute_sqlplus_once(request, request_id):
    """Keep refresh/retry of the same terminal command from executing it twice."""
    key = (request.user.pk, request_id)
    with _lock:
        entry = _sqlplus_requests.get(key)
        if entry is None:
            # Only completed requests expire; never evict a running command.
            cutoff = time.monotonic() - 86400
            for old_key, old_entry in list(_sqlplus_requests.items()):
                if old_entry.get('finished', float('inf')) < cutoff:
                    del _sqlplus_requests[old_key]
            if len(_sqlplus_requests) >= 10000:
                return json_error('Terminal recovery cache is full. Try again later.', 503)
            entry = {'lock': RLock(), 'body': request.body, 'response': None}
            _sqlplus_requests[key] = entry
    with entry['lock']:
        if entry['body'] != request.body:
            return json_error('Terminal request ID already belongs to another statement.', 409)
        if entry['response'] is None:
            response = _execute_sql_request(request)
            # No statement ran when the Oracle session was absent; allow retry.
            if response.status_code == 401 or (response.status_code == 503 and json.loads(response.content).get('connectionUnavailable')):
                return response
            entry['response'] = response
            entry['finished'] = time.monotonic()
        return entry['response']
_STATIC_FILES = {"index.html", "app.js", "styles.css", "output.css", "global-controls.js", "global-controls.css"}


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
    now = time.time()
    with _lock:
        expired = [sid for sid, value in _oracle_sessions.items() if now - value["last_activity"] > IDLE_SECONDS]
        for sid in expired:
            old = _oracle_sessions.get(sid)
            if old: _close_oracle_session(old)
        session = _oracle_sessions.get(key) if key else None
        token = request.COOKIES.get("ora_compiler_session", "")
        if session and request.user.is_authenticated and session.get("user_id") not in (None, request.user.pk):
            session = None
        if token and (not session or not request.user.is_authenticated):
            candidate = _oracle_session_tokens.get(token)
            if not candidate or candidate.get("independent_token") != token:
                candidate = None
                request._clear_compiler_session_cookie = True
            elif time.time() >= float(candidate.get("independent_expires_at") or 0):
                _close_oracle_session(candidate)
                candidate = None
                request._clear_compiler_session_cookie = True
            if candidate and candidate.get("independent_enabled"):
                if request.user.is_authenticated and candidate.get("user_id") != request.user.pk:
                    candidate = None
                elif not _independent_compiler_session_allowed(candidate.get("user_id")):
                    _close_oracle_session(candidate)
                    candidate = None
                    request._clear_compiler_session_cookie = True
                else:
                    from .models import PortalUser
                    owner = PortalUser.objects.filter(pk=candidate.get("user_id"), is_active=True).only(
                        "id", "role", "active_student_session_token"
                    ).first()
                    if not owner or owner.active_student_session_token != candidate.get("student_session_token", ""):
                        if candidate: _close_oracle_session(candidate)
                        candidate = None
                        request._clear_compiler_session_cookie = True
                    session = candidate
                    if session:
                        request._compiler_session_cookie = session.get("independent_token", "")
                    if session and not request.user.is_authenticated:
                        request._compiler_session_owner = owner
                        request._compiler_session_role = session.get("portal_role") or "STUDENT"
        if session and request.user.is_authenticated:
            if session.get("independent_enabled"):
                if _independent_compiler_session_allowed(request.user.pk):
                    _sync_independent_compiler_expiry(request, session)
                    request._compiler_session_cookie = session.get("independent_token", "")
                else:
                    for active_token, current in list(_oracle_session_tokens.items()):
                        if current is session: _oracle_session_tokens.pop(active_token, None)
                    session["independent_enabled"] = False
                    session.pop("independent_token", None)
                    request._clear_compiler_session_cookie = True
            else:
                _ensure_independent_compiler_token(request, session)
        elif session and not getattr(request, "_compiler_session_owner", None):
            session = None
        if session and touch:
            session["last_activity"] = now
        return session


def _close_oracle_session(session):
    if not session:
        return
    with _lock:
        for key, current in list(_oracle_sessions.items()):
            if current is session:
                _oracle_sessions.pop(key, None)
        for token, current in list(_oracle_session_tokens.items()):
            if current is session:
                _oracle_session_tokens.pop(token, None)
    for connection_name in ("sqlplus", "editor"):
        connection = session.get(connection_name)
        if connection:
            try: connection.close()
            except Exception: pass


def _independent_compiler_session_allowed(user_id):
    if not user_id:
        return False
    from django.core.cache import cache
    cache_key = f"independent-compiler-session:{user_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return bool(cached)
    from .models import Enrollment
    allowed = Enrollment.objects.filter(
        student_id=user_id, active=True,
        class_section__teacher_assignments__teacher__activity_preference__independent_compiler_sessions_enabled=True,
    ).exists()
    cache.set(cache_key, allowed, 10)
    return allowed


def _ensure_independent_compiler_token(request, session):
    user = request.user
    if getattr(user, "role", None) != "STUDENT":
        return
    if not _independent_compiler_session_allowed(user.pk):
        request._clear_compiler_session_cookie = True
        return
    with _lock:
        token = session.get("independent_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["independent_token"] = token
            session["independent_enabled"] = True
            session["user_id"] = user.pk
            session["student_session_token"] = getattr(user, "active_student_session_token", "")
            session["portal_role"] = "STUDENT"
            _oracle_session_tokens[token] = session
        _sync_independent_compiler_expiry(request, session)
    request._compiler_session_cookie = token


def _sync_independent_compiler_expiry(request, session):
    try:
        # get_expiry_date() is a prospective deadline and moves on every call.
        # Capture it only when the Django session will be saved, so compiler
        # requests cannot keep the independent token alive beyond portal login.
        if request.session.modified or not session.get("independent_portal_expires_at"):
            session["independent_portal_expires_at"] = request.session.get_expiry_date().timestamp()
            session["independent_expires_at_browser_close"] = request.session.get_expire_at_browser_close()
        expires_at = float(session.get("independent_portal_expires_at") or 0)

        # Student portal sessions also end on inactivity or exhausted daily
        # allowance. Heartbeats refresh these deadlines while the portal session
        # is valid; token-only requests can then reject expired sessions without
        # querying Oracle or Django's session store on every statement.
        allowance = getattr(request, "student_daily_allowance", None)
        attendance = getattr(request, "student_attendance", None)
        if allowance and allowance.get("restricted"):
            if attendance and attendance.last_activity_at:
                expires_at = min(expires_at, attendance.last_activity_at.timestamp() + 1800)
            remaining = allowance.get("remainingSeconds")
            if remaining is not None:
                expires_at = min(expires_at, time.time() + max(0, float(remaining)))
        session["independent_expires_at"] = expires_at
    except (AttributeError, TypeError, ValueError, OverflowError):
        session["independent_expires_at"] = time.time()
        session["independent_expires_at_browser_close"] = False


def _authorize_compiler_session_request(request):
    session = current_oracle_session(request)
    if not session:
        return None
    if request.user.is_authenticated:
        if session.get("user_id") not in (None, request.user.pk):
            return None
        return session
    owner = getattr(request, "_compiler_session_owner", None)
    if owner is None or not session.get("independent_enabled"):
        return None
    # A newer student login revokes the previous independent compiler token.
    from .models import PortalUser
    owner = PortalUser.objects.filter(
        pk=owner.pk, is_active=True, active_student_session_token=session.get("student_session_token", "")
    ).first()
    if not owner or not _independent_compiler_session_allowed(owner.pk):
        _close_oracle_session(session)
        request._clear_compiler_session_cookie = True
        return None
    request.user = owner
    request._compiler_session_role = session.get("portal_role") or "STUDENT"
    request._compiler_session_cookie = session.get("independent_token")
    return session


def _refresh_compiler_session_cookie(response, request):
    token = getattr(request, "_compiler_session_cookie", "")
    if token:
        session = _oracle_session_tokens.get(token)
        expires_at = float(session.get("independent_expires_at") or 0) if session else 0
        remaining = int(expires_at - time.time())
        if not session or remaining <= 0:
            response.delete_cookie("ora_compiler_session", samesite="Strict")
        else:
            cookie_options = {"httponly": True, "samesite": "Strict", "secure": request.is_secure()}
            if not session.get("independent_expires_at_browser_close"):
                cookie_options["max_age"] = remaining
            response.set_cookie("ora_compiler_session", token, **cookie_options)
    elif getattr(request, "_clear_compiler_session_cookie", False):
        response.delete_cookie("ora_compiler_session", samesite="Strict")
    return response


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


_SQLPLUS_CLIENT_COMMAND = re.compile(
    r"^(?:DESC(?:RIBE)?|SET|SHOW|DEFINE|UNDEFINE|PROMPT|REM(?:ARK)?|HELP|HISTORY|TIMING|"
    r"CLEAR|LIST|L|APPEND|CHANGE|C|INPUT|DEL|RUN|R|EXIT|QUIT|DISCONNECT|CONNECT|CONN|"
    r"START|GET|SAVE|SPOOL|@|\?)\b",
    re.I,
)


def split_script(source, sqlplus=False):
    from .sql_lexer import split_statements
    return split_statements(source, sqlplus, _SQLPLUS_CLIENT_COMMAND)


def open_oracle_connection(options):
    connection = oracledb.connect(**options)
    connection.call_timeout = ORACLE_CALL_TIMEOUT_MS
    return connection


def tune_oracle_cursor(cursor, rows=MAX_ROWS):
    """Match Oracle fetch batches to the bounded result sizes returned by the UI."""
    cursor.arraysize = rows
    cursor.prefetchrows = rows


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
    from .sql_lexer import bind_names
    names = bind_names(sql)
    values = {}
    for name in names:
        if name not in by_upper: raise ValueError(f"No value was supplied for bind variable :{name}.")
        values[name] = by_upper[name]
    return values


def json_value(value, oracle_type=None):
    if isinstance(value, (datetime, date)) and oracle_type == oracledb.DB_TYPE_DATE:
        months = ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC')
        return f'{value.day:02d}-{months[value.month-1]}-{value.year % 100:02d}'
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
    response = frontend_asset(request, "index.html")
    response["Cache-Control"] = "no-store"
    return response


@ensure_csrf_cookie
@login_required(login_url="/portal/")
def compiler_selection(request):
    from .models import CompilerOption
    from .portal_views import _active_portal_role, _available_portal_roles, _dashboard_navigation_items, _global_default_accent

    role = _active_portal_role(request)
    if role not in {"STUDENT", "TEACHER", "ADMIN"} and not request.user.is_superuser:
        if len(_available_portal_roles(request.user)) > 1:
            return redirect("/portal/choose-role/")
        return redirect("/portal/")

    compiler_options = list(CompilerOption.objects.filter(is_active=True))
    native_ready = False
    if any(compiler.engine in {'C', 'CPP'} for compiler in compiler_options):
        from .native_compiler import runner_status
        native_ready = runner_status()['ready']
    for compiler in compiler_options:
        native = compiler.engine in {'C', 'CPP'}
        compiler.workspace_url = f'/portal/compiler/{compiler.engine}/' if native else f'/oracle-sql/?engine={compiler.engine}' + ('' if request.user.has_oracle_credentials() else '&promptOracleLogin=1')
        compiler.availability_label = ('READY' if native_ready else 'EDITOR AVAILABLE · RUNNER SETUP NEEDED') if native else ('READY' if compiler.execution_ready else 'UNAVAILABLE')
        compiler.name = compiler.name.replace("SQL*Plus", "SQLCL").replace("SQL*PLUS", "SQLCL").replace("PL/SQL", "SQL WorkSheet")
        compiler.description = compiler.description.replace("SQL*Plus", "SQLCL").replace("SQL*PLUS", "SQLCL").replace("PL/SQL", "SQL WorkSheet")
    return render(request, "compiler_selection.html", {
        "compiler_options": compiler_options,
        "user": request.user,
        "has_saved_oracle_credentials": request.user.has_oracle_credentials(),
        "portal_role": role,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(role),
        "is_compiler_page": True,
        "is_profile_page": False,
        "dashboard_home_path": "/teacher" if role == "TEACHER" else "/portal/student/" if role == "STUDENT" else "/admin/",
    })


@ensure_csrf_cookie
@login_required(login_url="/portal/")
def compiler_workspace(request):
    response = frontend_asset(request, "index.html")
    response["Cache-Control"] = "no-store"
    return response


@ensure_csrf_cookie
def oracle_sql_workspace(request):
    from .portal_views import _active_portal_role
    if not request.user.is_authenticated and not _authorize_compiler_session_request(request):
        return redirect("/portal/")
    role = getattr(request, "_compiler_session_role", None) or _active_portal_role(request)
    response = render(request, "oracle_sql_workspace.html", {
        "is_teacher": role == "TEACHER",
        "is_student": role == "STUDENT",
        "can_manage_compiler_tabs": role in {"ADMIN", "TEACHER"} or request.user.is_superuser,
    })
    response["Cache-Control"] = "no-store"
    return _refresh_compiler_session_cookie(response, request)


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
@login_required(login_url="/portal/")
def connect_oracle(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 413 if str(exc) == "Request is too large." else 400)
    user, password, connect_string = payload.get("user"), payload.get("password"), payload.get("connectString")
    if not all(isinstance(value, str) and value.strip() for value in (user, connect_string)) or not isinstance(password, str) or not password:
        return json_error("Enter the Oracle username, password, and connect string.")
    save_credentials = payload.get("saveCredentials", False)
    if not isinstance(save_credentials, bool): return json_error("Invalid credential-saving option.")
    name = payload.get("connectionName", "")
    if not isinstance(name, str) or len(name.strip()) > 120:
        return json_error("Connection name must be at most 120 characters.")
    response = _open_oracle_session(request, user.strip(), password, connect_string.strip(), save_credentials=save_credentials)
    if save_credentials and response.status_code < 400:
        from .models import OracleConnectionProfile
        OracleConnectionProfile.objects.update_or_create(
            owner=request.user, name=name.strip() or f"{user.strip()} · {connect_string.strip()}"[:120],
            defaults={"username": user.strip(), "connect_string": connect_string.strip(),
                      "password_encrypted": request.user.oracle_password_encrypted},
        )
    return response


@csrf_exempt
@require_http_methods(["POST"])
def legacy_connect_oracle(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 413 if str(exc) == "Request is too large." else 400)
    user, password, connect_string = payload.get("user"), payload.get("password"), payload.get("connectString")
    if not all(isinstance(value, str) and value.strip() for value in (user, connect_string)) or not isinstance(password, str) or not password:
        return json_error("Enter the Oracle username, password, and connect string.")
    return _open_oracle_session(request, user.strip(), password, connect_string.strip(), save_credentials=None)


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
@login_required(login_url="/portal/")
def connect_saved_oracle(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    profile = request.user
    from .models import OracleConnectionProfile
    if request.method == "DELETE":
        try: payload = read_json(request)
        except ValueError as exc: return json_error(exc)
        selected = payload.get("connectionId")
        if not isinstance(selected, int) or isinstance(selected, bool): return json_error("Select a saved connection to delete.")
        deleted, _ = OracleConnectionProfile.objects.filter(owner=request.user, pk=selected).delete()
        if not deleted: return json_error("Saved connection not found.", 404)
        return JsonResponse({"deleted": True, "connectionId": selected})
    if request.method == "GET":
        return JsonResponse({
            "connections": [{"id": item.pk, "name": item.name, "user": item.username,
                             "connectString": item.connect_string} for item in profile.oracle_connections.order_by("name", "id")],
            "saved": profile.has_oracle_credentials(),
            "user": profile.oracle_username if profile.has_oracle_credentials() else "",
            "connectString": profile.oracle_connect_string if profile.has_oracle_credentials() else "",
        })
    try: payload = read_json(request) if request.body and request.content_type == "application/json" else {}
    except ValueError as exc: return json_error(exc)
    selected = payload.get("connectionId")
    if selected is not None:
        if not isinstance(selected, int) or isinstance(selected, bool): return json_error("Invalid saved connection.")
        saved = OracleConnectionProfile.objects.filter(owner=request.user, pk=selected).first()
        if not saved: return json_error("Saved connection not found.", 404)
        response = _open_oracle_session(request, saved.username, saved.get_oracle_password(), saved.connect_string, expose_saved_details=True)
        if response.status_code < 400:
            request.user.oracle_username = saved.username
            request.user.oracle_password_encrypted = saved.password_encrypted
            request.user.oracle_connect_string = saved.connect_string
            request.user.save(update_fields=("oracle_username", "oracle_password_encrypted", "oracle_connect_string"))
        return response
    if not profile.has_oracle_credentials():
        return JsonResponse({"connected": False, "saved": False})
    session_key = request.session.session_key or str(profile.pk)
    with _lock:
        if session_key not in _saved_connection_locks and len(_saved_connection_locks) >= 1000:
            return json_error("Connection coordinator is busy. Try again later.", 503)
        coordinator = _saved_connection_locks.setdefault(session_key, RLock())
    with coordinator:
        user, connect_string = profile.oracle_username, profile.oracle_connect_string
        existing = current_oracle_session(request, touch=False)
        if existing and existing.get("settings", {}).get("user", "").casefold() == user.casefold() and existing["settings"].get("connectString") == connect_string and any(existing.get(key) for key in ("sqlplus", "editor")):
            return JsonResponse({**existing.get("status", {}), "connected": True, "saved": True, "user": user, "connectString": connect_string})
        response = _open_oracle_session(request, user, profile.get_oracle_password(), connect_string, expose_saved_details=True)
        if response.status_code >= 400:
            try: message = json.loads(response.content).get("error", "Saved Oracle credentials could not connect.")
            except (json.JSONDecodeError, UnicodeDecodeError): message = "Saved Oracle credentials could not connect."
            return json_error(message, response.status_code, saved=True, user=user, connectString=connect_string)
        return response


def _open_oracle_session(request, user, password, connect_string, save_credentials=None, expose_saved_details=False):
    key = request.META.get("REMOTE_ADDR", "unknown")
    now = time.time()
    attempts = [stamp for stamp in _connect_attempts.get(key, []) if now - stamp < 60]
    if len(attempts) >= 10:
        _connect_attempts[key] = attempts
        return json_error("Too many connection attempts. Wait a minute and try again.", 429)
    attempts.append(now); _connect_attempts[key] = attempts
    if not user or not password or not connect_string:
        return json_error("Saved Oracle credentials are incomplete.")
    with _lock:
        if len(_oracle_sessions) >= 100 and not current_oracle_session(request, touch=False):
            return json_error("The server has reached its active login limit.", 503)
    connection = None
    try:
        options = connect_options({"user": user, "password": password, "connectString": connect_string})
        connection = open_oracle_connection(options)
        result = connect_info(connection, connect_string, user)
        if save_credentials is not None:
            if save_credentials:
                request.user.set_oracle_credentials(user, password, connect_string)
            else:
                request.user.clear_oracle_credentials()
            request.user.save(update_fields=("oracle_username", "oracle_password_encrypted", "oracle_connect_string"))
        old_session_key = request.session.session_key
        prior = current_oracle_session(request, touch=False)
        if prior:
            _close_oracle_session(prior)
        if not request.session.session_key:
            request.session.save()
        with _lock:
            if old_session_key and old_session_key != request.session.session_key:
                _oracle_sessions.pop(old_session_key, None)
        result["navigatorCacheId"] = secrets.token_urlsafe(16)
        result["connectString"] = connect_string.strip()
        workspace_id = request.COOKIES.get("ora_workspace") or secrets.token_urlsafe(32)
        request.session["oracle_connected"] = True
        session_key = request.session.session_key
        from .portal_views import _active_portal_role
        portal_role = _active_portal_role(request)
        state = {"settings": {"user": user.strip(), "password": password, "connectString": connect_string.strip()}, "workspace_id": workspace_id, "sqlplus": connection, "editor": None, "dbms_output_enabled": {"sqlplus": {"enabled": False}, "editor": {"enabled": False}}, "status": result, "last_activity": now, "user_id": request.user.pk, "portal_role": portal_role}
        with _lock:
            _oracle_sessions[session_key] = state
        if getattr(request.user, "role", None) == "STUDENT":
            _ensure_independent_compiler_token(request, state)
        connection = None
        response_data = {**result, "saved": True, "user": user, "connectString": connect_string} if expose_saved_details else result
        response = JsonResponse(response_data)
        response.set_cookie("ora_workspace", workspace_id, max_age=31536000, httponly=True, samesite="Strict", secure=request.is_secure())
        _refresh_compiler_session_cookie(response, request)
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
        _close_oracle_session(session)
    elif request.session.session_key:
        with _lock: _oracle_sessions.pop(request.session.session_key, None)
    # Oracle disconnect must preserve the authenticated portal session.
    request.session.pop("oracle_connected", None)
    # Database disconnect is not an authentication change. Rotating this key
    # races with in-flight portal heartbeats using the existing session cookie.
    response = JsonResponse({"connected": False})
    response.delete_cookie("ora_workspace", samesite="Strict")
    response.delete_cookie("ora_compiler_session", samesite="Strict")
    return response


def _oracle_transaction_pending(connection):
    if connection is None: return None
    try:
        pending = connection.transaction_in_progress
        return pending if isinstance(pending, bool) else None
    except Exception:
        return None


def _oracle_transactions(session):
    return {"worksheet": _oracle_transaction_pending(session.get("editor")),
            "sqlcl": _oracle_transaction_pending(session.get("sqlplus"))}


@require_http_methods(["GET"])
def oracle_status(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    session = current_oracle_session(request)
    if not session: return _refresh_compiler_session_cookie(JsonResponse({"connected": False}), request)
    status = session.setdefault("status", {})
    status.setdefault("navigatorCacheId", secrets.token_urlsafe(16))
    operation_lock = session.setdefault("operation_lock", RLock())
    if not operation_lock.acquire(blocking=False):
        return _refresh_compiler_session_cookie(JsonResponse({**status, "connected": True, "busy": True}), request)
    try:
        key = "sqlplus" if session.get("sqlplus") else "editor"
        connection = session.get(key)
        if not connection and session.get("independent_enabled"):
            try:
                connection = open_oracle_connection(connect_options(session["settings"]))
                session[key] = connection
                session.setdefault("dbms_output_enabled", {})[key] = {"enabled": False}
            except Exception:
                return _refresh_compiler_session_cookie(JsonResponse({"connected": False}), request)
        if not connection: return _refresh_compiler_session_cookie(JsonResponse({"connected": False}), request)
        try:
            with connection.cursor() as cursor: cursor.execute("SELECT 1 FROM dual")
        except Exception as exc:
            broken = bool(re.search(r"\b(?:DPY-(?:1001|4011)|NJS-(?:003|500|511|521)|ORA-(?:03113|03114|03135|12537|12547|12570|12571))\b", str(exc)))
            if not broken:
                return _refresh_compiler_session_cookie(JsonResponse({**status, "connected": True, "statusWarning": "Oracle health check could not complete."}), request)
            try: connection.close()
            except Exception: pass
            session[key] = None
            if session.get("independent_enabled"):
                try:
                    session[key] = open_oracle_connection(connect_options(session["settings"]))
                    session.setdefault("dbms_output_enabled", {})[key] = {"enabled": False}
                    return _refresh_compiler_session_cookie(JsonResponse({**status, "connected": True, "reconnected": True}), request)
                except Exception:
                    pass
            # A stale terminal must never discard an independent editor transaction.
            return _refresh_compiler_session_cookie(JsonResponse({**status, "connected": bool(session.get("editor") or session.get("sqlplus"))}), request)
        return _refresh_compiler_session_cookie(JsonResponse({**status, "connected": True, "transactions": _oracle_transactions(session)}), request)
    finally:
        operation_lock.release()


DBMS_OUTPUT_CHUNK_SIZE = 500


def _explain_plan_lines(options, sql, binds=None):
    """Explain one statement on an isolated connection and return DBMS_XPLAN lines."""
    statement_id = "WBP" + uuid.uuid4().hex[:20].upper()
    connection = open_oracle_connection(connect_options(options))
    connection.call_timeout = ORACLE_CALL_TIMEOUT_MS
    # This connection is used only for the disposable PLAN_TABLE rows. Never
    # touch the interactive SQLCL/worksheet connection or its open transaction.
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            tune_oracle_cursor(cursor)
            cursor.execute(
                f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {sql}",
                driver_binds(sql, binds),
            )
            cursor.execute(
                "SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY(NULL, :statement_id, 'TYPICAL +PREDICATE +ALIAS'))",
                {"statement_id": statement_id},
            )
            lines = [str(row[0] or "") for row in cursor.fetchmany(1000)]
            cursor.execute(
                "SELECT id, parent_id, operation, options, object_owner, object_name, cardinality, cost "
                "FROM PLAN_TABLE WHERE statement_id = :statement_id ORDER BY id",
                {"statement_id": statement_id},
            )
            plan_rows = cursor.fetchall()
        nodes_by_id = {}
        for node_id, parent_id, operation, options_text, owner, object_name, cardinality, cost in plan_rows:
            operation_name = " ".join(value for value in (operation, options_text) if value)
            if object_name:
                operation_name += f" · {owner + '.' if owner else ''}{object_name}"
            nodes_by_id[node_id] = {
                "id": node_id,
                "parentId": parent_id,
                "operation": operation_name,
                "estimatedRows": cardinality,
                "cost": cost,
                "children": [],
            }
        roots = []
        for node in nodes_by_id.values():
            parent = nodes_by_id.get(node["parentId"])
            (parent["children"] if parent else roots).append(node)
        return {"lines": lines, "tree": roots}
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM PLAN_TABLE WHERE statement_id = :statement_id", {"statement_id": statement_id})
        except Exception:
            # A missing/privilege-restricted PLAN_TABLE cleanup should not
            # discard a plan that DBMS_XPLAN already returned.
            pass
        try:
            connection.close()
        except Exception:
            pass


def _unwrap_explain_plan(sql):
    prefix = re.match(
        r"^EXPLAIN\s+PLAN(?:\s+SET\s+STATEMENT_ID\s*=\s*(?:'[^']*'|[\w$#]+))?\s+FOR\s+",
        sql,
        re.I,
    )
    return sql[prefix.end():].strip() if prefix else sql


def execute_one(connection, sql, binds, auto_commit, serveroutput=True, serveroutput_state=None):
    connection.autocommit = auto_commit
    with connection.cursor() as cursor:
        tune_oracle_cursor(cursor)
        from .sql_lexer import is_plsql_statement
        is_plsql = is_plsql_statement(sql)
        if is_plsql and serveroutput and (serveroutput_state is None or not serveroutput_state.get("enabled")):
            cursor.execute("BEGIN DBMS_OUTPUT.ENABLE(NULL); END;")
            if serveroutput_state is not None:
                serveroutput_state["enabled"] = True
        parameters = driver_binds(sql, binds)
        from .sql_lexer import tokens
        first_token = next((token for token in tokens(sql) if token["kind"] != "comment"), None)
        if first_token and first_token["kind"] == "word" and first_token["text"].upper() == "RENAME":
            # python-oracledb 3.4.x Thin omits RENAME from its DDL classifier.
            # A reused DDL cursor can report success without repeating the rename.
            # First discard a handle cached before this safeguard was installed.
            # Preparing alone can still reuse that old handle once; closing its
            # owning cursor before preparing again guarantees a fresh execution.
            with connection.cursor() as eviction_cursor:
                eviction_cursor.prepare(sql, cache_statement=False)
            cursor.prepare(sql, cache_statement=False)
            cursor.execute(None, parameters)
        else:
            cursor.execute(sql, parameters)
        execution_warning = getattr(cursor, "warning", None)
        description = cursor.description or []
        columns = [{"name": item[0], "type": getattr(item[1], "name", "")} for item in description]
        rows = [[json_value(value, description[index][1]) for index, value in enumerate(row)] for row in cursor.fetchmany(MAX_ROWS)] if columns else []
        rows_affected = cursor.rowcount if cursor.rowcount >= 0 and not columns else None
        output_lines = []
        if is_plsql and serveroutput:
            lines_var = cursor.arrayvar(str, DBMS_OUTPUT_CHUNK_SIZE)
            num_lines_var = cursor.var(int)
            num_lines_var.setvalue(0, DBMS_OUTPUT_CHUNK_SIZE)
            while True:
                cursor.callproc("DBMS_OUTPUT.GET_LINES", (lines_var, num_lines_var))
                line_count = num_lines_var.getvalue()
                output_lines.extend(line or "" for line in lines_var.getvalue()[:line_count])
                if line_count < DBMS_OUTPUT_CHUNK_SIZE:
                    break
                num_lines_var.setvalue(0, DBMS_OUTPUT_CHUNK_SIZE)
        compilation_errors = []
        create_match = re.match(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(MATERIALIZED\s+VIEW|PACKAGE\s+BODY|TYPE\s+BODY|PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE|VIEW)\s+(\"(?:\"\"|[^\"])+\"|[\w$#]+)", sql, re.I)
        if create_match and getattr(execution_warning, "full_code", "") == "DPY-7000":
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


def sqlplus_feedback(sql, rows_affected, has_result=False):
    if has_result:
        return ""
    command = re.sub(r"^\s*(?:EXEC|EXECUTE)\s+", "BEGIN ", sql, flags=re.I).lstrip().upper()
    if re.match(r"^(?:DECLARE|BEGIN)\b", command):
        return "PL/SQL procedure successfully completed."
    if re.match(r"^RENAME\b", command):
        return "Object renamed."
    if re.match(r"^COMMIT\b", command):
        return "Commit complete."
    if re.match(r"^ROLLBACK\b", command):
        return "Rollback complete."
    if rows_affected is not None and rows_affected >= 0:
        count = f"{rows_affected} row{'s' if rows_affected != 1 else ''}"
        if re.match(r"^INSERT\b", command): return f"{count} created."
        if re.match(r"^UPDATE\b", command): return f"{count} updated."
        if re.match(r"^DELETE\b", command): return f"{count} deleted."
        if re.match(r"^MERGE\b", command): return f"{count} merged."
    match = re.match(r"^(CREATE|DROP|ALTER|TRUNCATE|RENAME)\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?(TABLE|INDEX|VIEW|SEQUENCE|SYNONYM|TRIGGER|PROCEDURE|FUNCTION|PACKAGE|TYPE|MATERIALIZED\s+VIEW)\b", command)
    if match:
        operation, object_type = match.groups()
        label = {"MATERIALIZED VIEW": "Materialized view"}.get(object_type, object_type.title())
        verb = {"CREATE": "created", "DROP": "dropped", "ALTER": "altered", "TRUNCATE": "truncated", "RENAME": "renamed"}.get(operation)
        if verb: return f"{label} {verb}."
    return "Statement executed successfully."


@csrf_exempt
@require_http_methods(["POST"])
def execute_sql(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated:
        _authorize_compiler_session_request(request)
    if not request.user.is_authenticated:
        return _refresh_compiler_session_cookie(json_error("Sign in again to start a compiler session.", 401), request)
    request_id = request.headers.get('X-Terminal-Request-Id', '')
    if request_id:
        if not same_origin(request): return json_error('Cross-origin API requests are blocked.', 403)
        if not re.fullmatch(r'[A-Za-z0-9-]{16,80}', request_id): return json_error('Invalid terminal request ID.')
        return _refresh_compiler_session_cookie(_execute_sqlplus_once(request, request_id), request)
    return _refresh_compiler_session_cookie(_execute_sql_request(request), request)


@csrf_exempt
@require_http_methods(["POST"])
def explain_sql(request):
    """Return an estimated Oracle execution plan and concise plan diagnostics."""
    if not same_origin(request):
        return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated:
        _authorize_compiler_session_request(request)
    if not request.user.is_authenticated:
        return _refresh_compiler_session_cookie(json_error("Sign in again to start a compiler session.", 401), request)
    try:
        payload = read_json(request)
    except ValueError as exc:
        return json_error(exc, 413 if str(exc) == "Request is too large." else 400)
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return json_error("Provide a SQL query to explain.")
    statements = [statement for statement in split_script(sql) if statement]
    if len(statements) != 1:
        return json_error("Explain Plan accepts one SQL statement at a time.")
    from .sql_lexer import prepare_statement, tokens
    statement = prepare_statement(statements[0])
    first_sql_token = next((token for token in tokens(statement) if token["kind"] != "comment"), None)
    if first_sql_token is None:
        return json_error("Provide a SQL query to explain.")
    statement = _unwrap_explain_plan(statement[first_sql_token["start"]:])
    blocked = blocked_admin_command(statement)
    if blocked:
        return json_error(f"{blocked} is disabled by the workbench security policy.", 403)
    if not re.match(r"^(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b", statement, re.I):
        return json_error("Explain Plan supports SELECT and DML statements only.")
    session = current_oracle_session(request)
    if not session:
        return _refresh_compiler_session_cookie(json_error("Connect to Oracle first.", 401), request)
    operation_lock = session.setdefault("operation_lock", RLock())
    operation_lock.acquire()
    try:
        # Use an isolated connection so diagnostics cannot affect pending work.
        plan_data = _explain_plan_lines(session["settings"], statement, payload.get("binds"))
        plan_lines = plan_data["lines"]
        plan_text = "\n".join(plan_lines)
        diagnostics = []
        if re.search(r"TABLE ACCESS FULL", plan_text, re.I):
            diagnostics.append({"severity": "review", "message": "The plan includes a full table scan. This can be appropriate for small tables or queries returning many rows; review filters and indexes if the table is large."})
        if re.search(r"MERGE JOIN CARTESIAN|CARTESIAN", plan_text, re.I):
            diagnostics.append({"severity": "warning", "message": "The plan includes a Cartesian join. Check that each joined table has the intended join condition."})
        if not diagnostics:
            diagnostics.append({"severity": "info", "message": "No common plan warning was detected. The plan contains optimizer estimates, not measured execution statistics."})
        response = JsonResponse({"statement": statement, "plan": plan_lines, "planTree": plan_data["tree"], "diagnostics": diagnostics, "message": "Estimated execution plan"})
        return _refresh_compiler_session_cookie(response, request)
    except Exception as exc:
        message = getattr(exc, "message", str(exc))
        return _refresh_compiler_session_cookie(json_error(message), request)
    finally:
        operation_lock.release()


def _execute_sql_request(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 413 if str(exc) == "Request is too large." else 400)
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip(): return json_error("Provide a SQL statement.")
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    started = time.monotonic(); connection = None; failing = ""
    statement_history_recorded = False
    history_edit_source = payload.get("editSource")
    history_input_method = (history_edit_source.get("inputMethod", "")
                            if isinstance(history_edit_source, dict) else "")
    history_source_runs = history_edit_source.get("inputSourceMap", []) if isinstance(history_edit_source, dict) else []
    history_source_sql = sql
    history_source_cursor = 0
    statements = split_script(sql, sqlplus=bool(payload.get("sqlplus")))
    statements = [item for item in statements if item]
    if not statements: return json_error("Provide a SQL statement.")
    if payload.get("executionMode") == "statement" and len(statements) != 1:
        return json_error("Run statement accepts one statement. Use a selection or Run script for a batch.")
    for statement in statements:
        blocked = blocked_admin_command(statement)
        if blocked: return json_error(f"{blocked} is disabled by the workbench security policy.", 403)
    connection_key = "sqlplus" if payload.get("sqlplus") else "editor"
    sqlplus_settings = session.setdefault("sqlplus_settings", {"autocommit": bool(payload.get("autoCommit")), "serveroutput": True, "feedback": True, "heading": True, "linesize": 80, "pagesize": 14})
    if payload.get("sqlplus") and isinstance(payload.get("autoCommit"), bool):
        sqlplus_settings["autocommit"] = payload["autoCommit"]
    operation_lock = session.setdefault("operation_lock", RLock())
    operation_lock.acquire()
    try:
        connection = session.get(connection_key)
        if connection:
            try:
                healthy = connection.is_healthy()
            except Exception:
                healthy = False
            if not healthy:
                try: connection.close()
                except Exception: pass
                connection = None
                session[connection_key] = None
                session.setdefault("dbms_output_enabled", {})[connection_key] = {"enabled": False}
        if not connection:
            connection = open_oracle_connection(connect_options(session["settings"]))
            session[connection_key] = connection
            session.setdefault("dbms_output_enabled", {})[connection_key] = {"enabled": False}
        columns, rows, affected, output, messages, statement_results, execution_errors = [], [], None, [], [], [], []
        for raw in statements:
            display_statement = raw.strip()
            history_source_start = history_source_sql.find(display_statement, history_source_cursor) if display_statement else -1
            if history_source_start >= 0:
                history_source_cursor = history_source_start + len(display_statement)
            from .sql_lexer import prepare_statement
            statement = prepare_statement(raw)
            if not statement: continue
            # Classify commands from their first SQL token, not a leading note.
            # Comments copied with function examples must not become SP2-0734.
            from .sql_lexer import tokens
            first_sql_token = next((token for token in tokens(statement) if token["kind"] != "comment"), None)
            if first_sql_token is None: continue
            statement = statement[first_sql_token["start"]:]
            failing = statement
            statement_started = time.monotonic()
            statement_status, statement_row_count, statement_message = "success", 0, ""
            try:
                explain_statement = _unwrap_explain_plan(statement)
                if explain_statement != statement:
                    explain_token = next((token for token in tokens(explain_statement) if token["kind"] != "comment"), None)
                    if explain_token is None:
                        raise ValueError("EXPLAIN PLAN FOR requires a SQL statement.")
                    explain_statement = explain_statement[explain_token["start"]:]
                    if not re.match(r"^(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b", explain_statement, re.I):
                        raise ValueError("EXPLAIN PLAN supports SELECT and DML statements only.")
                    blocked = blocked_admin_command(explain_statement)
                    if blocked:
                        raise ValueError(f"{blocked} is disabled by the workbench security policy.")
                    plan_data = _explain_plan_lines(session["settings"], explain_statement, payload.get("binds"))
                    plan_lines = plan_data["lines"]
                    result_columns = [{"name": "PLAN_TABLE_OUTPUT", "type": "VARCHAR2"}]
                    result_rows = [[line] for line in plan_lines]
                    columns, rows = result_columns, result_rows
                    message = f"Estimated execution plan ({len(plan_lines)} lines)"
                    statement_row_count, statement_message = len(plan_lines), message
                    messages.append(message)
                    statement_results.append({
                        "statement": display_statement,
                        "columns": result_columns,
                        "rows": result_rows,
                        "planTree": plan_data["tree"],
                        "message": message,
                    })
                    continue
                if not payload.get("sqlplus"):
                    from .sql_lexer import out_bind_names
                    output_binds = sorted(out_bind_names(statement))
                    if output_binds:
                        names = ", ".join(f":{name}" for name in output_binds)
                        raise ValueError(f"OUT bind variable(s) {names} are not supported by the interactive compiler. Use input binds or assign the value to a table column.")
                describe = re.fullmatch(r'(?:DESC|DESCRIBE)\s+([\w$#\"]+)(?:\.([\w$#\"]+))?', statement, re.I)
                if describe:
                    owner = describe.group(1).strip('"').upper() if describe.group(2) else None
                    name = describe.group(2).strip('"').upper() if describe.group(2) else describe.group(1).strip('"').upper()
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT column_name, nullable, data_type, data_length, char_length, char_used, data_precision, data_scale FROM all_tab_columns WHERE owner = NVL(:owner, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')) AND table_name = :name ORDER BY column_id", {"owner": owner, "name": name})
                        described = cursor.fetchall()
                    if not described:
                        raise ValueError(f"ORA-04043: object {f'{owner}.' if owner else ''}{name} does not exist or is not accessible.")
                    result_columns = [{"name": label, "type": ""} for label in ("Name", "Null?", "Type")]
                    result_rows = []
                    for column_name, nullable, data_type, data_length, char_length, char_used, precision, scale in described:
                        if data_type in {"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2"}:
                            length = char_length if char_used == "C" else data_length
                            data_type = f"{data_type}({length}{' CHAR' if char_used == 'C' else ''})"
                        elif data_type == "NUMBER" and precision is not None:
                            data_type = f"NUMBER({precision}{f',{scale}' if scale else ''})"
                        result_rows.append([column_name, "" if nullable == "Y" else "NOT NULL", data_type])
                    columns, rows = result_columns, result_rows
                    message = f"Object {f'{owner}.' if owner else ''}{name} described"
                    statement_row_count, statement_message = len(result_rows), message
                    messages.append(message)
                    statement_results.append({"statement": display_statement, "columns": result_columns, "rows": result_rows, "message": message})
                    continue
                if payload.get("sqlplus"):
                    from .sql_lexer import bind_names
                    declared_binds = session.setdefault('sqlplus_bind_variables', {})
                    if re.fullmatch(r"SHOW\s+USER", statement, re.I):
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT USER FROM dual")
                            current_user = cursor.fetchone()[0]
                        result_columns = [{"name": "USER", "type": "VARCHAR2"}]
                        result_rows = [[current_user]]
                        columns, rows = result_columns, result_rows
                        message = f"Current user: {current_user}"
                        statement_row_count, statement_message = 1, message
                        messages.append(message)
                        statement_results.append({"statement": display_statement, "columns": result_columns, "rows": result_rows, "message": message})
                        continue
                    declaration = re.fullmatch(r'VAR(?:IABLE)?\s+([A-Za-z][A-Za-z0-9_$#]*)\s+(NUMBER|VARCHAR2|CHAR|DATE)(?:\(\d+\))?', statement, re.I)
                    if declaration:
                        declared_binds[declaration.group(1).upper()] = None
                        message = f'Bind variable {declaration.group(1).upper()} declared.'
                        statement_message = message
                        messages.append(message)
                        statement_results.append({'statement': display_statement, 'message': message})
                        continue
                    missing_binds = sorted(bind_names(statement) - declared_binds.keys())
                    if missing_binds:
                        raise ValueError(f'SP2-0552: Bind variable "{missing_binds[0]}" not declared.\nHelp: https://docs.oracle.com/error-help/db/sp2-0552/')
                    if re.fullmatch(r'\d+', statement):
                        raise ValueError('SP2-0223: No lines in SQL buffer.\nHelp: https://docs.oracle.com/error-help/db/sp2-0223/')
                    if not re.match(r'^(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|RENAME|GRANT|REVOKE|COMMENT|ANALYZE|EXPLAIN|CALL|BEGIN|DECLARE|COMMIT|ROLLBACK|SAVEPOINT|SET|DESC|DESCRIBE|SHOW|DEFINE|UNDEFINE|ACCEPT|PROMPT|REM|REMARK|HELP|HISTORY|TIMING|CLEAR|LIST|L|APPEND|CHANGE|C|INPUT|DEL|RUN|R|EXIT|QUIT|DISCONNECT|CONNECT|CONN|START|GET|SAVE|SPOOL|EXEC|EXECUTE|VARIABLE|PRINT|COLUMN|BREAK|COMPUTE|TTITLE|BTITLE|WHENEVER|PAUSE|STORE)\b|^[@/?]', statement, re.I):
                        preview = statement.splitlines()[0][:10]
                        raise ValueError(f'SP2-0734: unknown command beginning "{preview}..." - rest of line ignored.\nHelp: https://docs.oracle.com/error-help/db/sp2-0734/')
                    set_match = re.fullmatch(r"SET\s+(LINESIZE|PAGESIZE|AUTOCOMMIT|SERVEROUTPUT|FEEDBACK|HEADING)\s+(\S+)", statement, re.I)
                    if set_match:
                        setting, value = set_match.group(1).lower(), set_match.group(2)
                        if setting in {"linesize", "pagesize"}:
                            if not value.isdigit() or int(value) < 1:
                                raise ValueError(f"SET {setting.upper()} expects a positive whole number.")
                            sqlplus_settings[setting] = int(value)
                        else:
                            if value.upper() not in {"ON", "OFF"}:
                                raise ValueError(f"SET {setting.upper()} expects ON or OFF.")
                            sqlplus_settings[setting] = value.upper() == "ON"
                        message = f"{setting.upper()} set to {value.upper()}"
                        statement_message = message
                        messages.append(message)
                        statement_results.append({"statement": display_statement, "message": message})
                        continue
                result_columns, result_rows, rows_affected, output_lines, compilation_errors = execute_one(
                    connection,
                    statement,
                    session.get('sqlplus_bind_variables', {}) if payload.get('sqlplus') else payload.get("binds"),
                    sqlplus_settings["autocommit"] if payload.get("sqlplus") else bool(payload.get("autoCommit")),
                    sqlplus_settings["serveroutput"] if payload.get("sqlplus") else True,
                    session.setdefault("dbms_output_enabled", {}).setdefault(connection_key, {"enabled": False}),
                )
                if compilation_errors:
                    error_text = "Oracle compilation failed:\n" + "\n".join(f"{item['message']} (line {item['line']}, column {item['column']})" for item in compilation_errors)
                    if connection_key == "editor":
                        try: connection.rollback()
                        except Exception: pass
                    raise ValueError(error_text)
                if result_columns: columns, rows = result_columns, result_rows
                if rows_affected is not None: affected = rows_affected
                output.extend(output_lines)
                message = (f"Query completed ({len(result_rows)} rows)" if result_columns else
                           sqlplus_feedback(statement, rows_affected) if payload.get("sqlplus") else
                           f"Statement {len(messages)+1} executed")
                messages.append(message)
                statement_row_count = len(result_rows) if result_columns else max(0, rows_affected or 0)
                statement_message = message
                statement_results.append({"statement": display_statement, "columns": result_columns, "rows": result_rows, "outputLines": output_lines, "rowsAffected": rows_affected, "message": message})
            except Exception as statement_error:
                statement_status = "error"
                statement_message = getattr(statement_error, "message", str(statement_error))
                broken_statement = bool(re.search(r"\b(?:DPY-(?:1001|4011)|NJS-(?:003|500|511|521)|ORA-(?:03113|03114|03135|12537|12547|12570|12571))\b", str(statement_error)))
                if not payload.get("sqlplus") or broken_statement: raise
                error_text = getattr(statement_error, "message", str(statement_error))
                execution_errors.append({"statement": display_statement, "error": error_text})
                statement_results.append({"statement": display_statement, "error": error_text})
            finally:
                statement_duration_ms = round((time.monotonic()-statement_started)*1000)
                if statement_results and statement_results[-1].get("statement") == display_statement:
                    statement_results[-1]["durationMs"] = statement_duration_ms
                statement_edit_source = dict(history_edit_source) if isinstance(history_edit_source, dict) else {}
                statement_edit_source["inputMethod"] = history_input_method
                statement_edit_source["inputSourceMap"] = (
                    _validated_input_source_map(history_source_runs, history_source_start, history_source_start + len(display_statement))
                    if history_source_start >= 0 else []
                )
                _record_compiler_query(request, display_statement, connection_key, statement_status,
                                       statement_duration_ms, statement_row_count,
                                       statement_message, statement_edit_source)
                # Input metrics belong to the submitted batch, not every statement.
                history_edit_source = None
                statement_history_recorded = True
        duration_ms = round((time.monotonic()-started)*1000)
        from .object_refresh import metadata_refresh
        plan_tree = next((item["planTree"] for item in reversed(statement_results) if "planTree" in item), None)
        return JsonResponse({"metadataRefresh": metadata_refresh(statement_results), "columns": columns, "rows": rows, "outputLines": output, "rowsAffected": affected, "queryPlanTree": plan_tree,
            "truncated": bool(columns and len(rows) == MAX_ROWS), "maxRows": MAX_ROWS,
            "durationMs": duration_ms, "message": " · ".join(messages), "statementsExecuted": len(messages),
            "statementResults": statement_results, "executionErrors": execution_errors,
            "transactionPending": _oracle_transaction_pending(connection),
            "sqlPlusAutoCommit": sqlplus_settings["autocommit"] if payload.get("sqlplus") else None,
            "sqlPlusHeading": sqlplus_settings["heading"], "sqlPlusFeedback": sqlplus_settings["feedback"]})
    except Exception as exc:
        message = getattr(exc, "message", str(exc)); code = getattr(exc, "code", None); offset = getattr(exc, "offset", None)
        if connection is None and not statement_history_recorded:
            return json_error(message, 503, connectionUnavailable=True)
        broken = bool(re.search(r"\b(?:DPY-(?:1001|4011)|NJS-(?:003|500|511|521)|ORA-(?:03113|03114|03135|12537|12547|12570|12571))\b", str(message)))
        if broken and connection:
            try: connection.close()
            except Exception: pass
            session[connection_key] = None
            session.setdefault("dbms_output_enabled", {})[connection_key] = {"enabled": False}
        elif connection and connection_key == "editor":
            try: connection.rollback()
            except Exception: pass
        if not statement_history_recorded:
            _record_compiler_query(request, failing or sql, connection_key, "error", round((time.monotonic()-started)*1000), 0, message, payload.get("editSource"))
        return json_error(message, 400, code=code, offset=offset, statement=failing or None, disconnected=broken)

    finally:
        operation_lock.release()


def _record_compiler_query(request, sql, connection_key, status, duration_ms, row_count, message="", edit_source=None):
    if not request.user.is_authenticated:
        return
    try:
        from .models import AttendanceSession, CompilerActivitySlice, CompilerExecutionEvent, CompilerQueryHistory
        from .portal_views import _active_portal_role
        if (getattr(request, "_compiler_session_role", None) or _active_portal_role(request)) not in {"STUDENT", "TEACHER"}:
            return
        source = "SQL*Plus" if connection_key == "sqlplus" else "PL/SQL"
        elapsed = max(0, int(duration_ms))
        rows = max(0, int(row_count))
        edit_source = edit_source if isinstance(edit_source, dict) else {}
        def metric(name):
            value = edit_source.get(name, 0)
            if isinstance(value, bool): return 0
            try: return min(10_000_000, max(0, int(value)))
            except (TypeError, ValueError, OverflowError): return 0
        edit_metrics = {
            "typed_chars": metric("typedChars"),
            "pasted_chars": metric("pastedChars"),
            "paste_count": metric("pasteCount"),
            "edits_after_paste": metric("editsAfterPaste"),
        }
        input_method = str(edit_source.get("inputMethod", "")).strip().lower()
        if input_method not in {"typed", "pasted", "file", "history_recall", "daily_task", "mixed", "internal_copy", "remote_code"}:
            input_method = ""
        input_source_map = _validated_input_source_map(edit_source.get("inputSourceMap", []), 0, min(len(str(sql)), MAX_BODY))
        pasted_texts = _validated_pasted_texts(edit_source.get("pasteTexts", []))
        CompilerQueryHistory.objects.create(
            student=request.user, source=source, sql=redact_text(str(sql)[:MAX_BODY], bare_token=True), status=status,
            duration_ms=elapsed, row_count=rows, message=str(message or "")[:4000],
            input_method=input_method, input_source_map=input_source_map,
            pasted_texts=pasted_texts, **edit_metrics,
        )
        attendance_id = request.session.get("attendance_session_id")
        attendance = AttendanceSession.objects.filter(pk=attendance_id, user=request.user).first() if attendance_id else None
        if attendance and attendance.logout_at and attendance.logout_estimated:
            attendance.logout_at = None
            attendance.logout_estimated = False
            attendance.end_reason = ""
        if attendance and attendance.logout_at is None:
            now = timezone.now()
            attendance.last_activity_at = now
            attendance.last_heartbeat_at = now
            attendance.is_active_now = True
            attendance.compiler_active = True
            attendance.current_page = "compiler"
            attendance.save(update_fields=("last_activity_at", "last_heartbeat_at", "is_active_now", "compiler_active", "current_page", "logout_at", "logout_estimated", "end_reason"))
            current_slice = attendance.activity_slices.filter(ended_at__isnull=True).order_by("-started_at", "-id").first()
            if not current_slice:
                current_slice = CompilerActivitySlice.objects.create(
                    attendance_session=attendance, class_section=attendance.class_section, lab=attendance.lab,
                    started_at=now, last_heartbeat_at=now, last_activity_at=now,
                    current_page="compiler", is_active_now=True, compiler_active=True,
                )
            else:
                current_slice.last_heartbeat_at = now
                current_slice.last_activity_at = now
                current_slice.current_page = "compiler"
                current_slice.is_active_now = True
                current_slice.compiler_active = True
                current_slice.save(update_fields=("last_heartbeat_at", "last_activity_at", "current_page", "is_active_now", "compiler_active"))
        CompilerExecutionEvent.objects.create(
            student=request.user, attendance_session=attendance,
            class_section=attendance.class_section if attendance else None,
            lab=attendance.lab if attendance else None, source=source, status=status,
            duration_ms=elapsed, row_count=rows, **edit_metrics,
        )
    except Exception:
        # Query execution must still return its Oracle result if history storage is unavailable.
        pass


def _teacher_student_ids(user):
    from .models import Enrollment, PortalUser
    if user.can_view_all_compiler_activity:
        return PortalUser.objects.filter(role=PortalUser.Role.STUDENT).values_list("id", flat=True)
    return Enrollment.objects.filter(
        class_section__teacher_assignments__teacher=user,
        student__role="STUDENT",
    ).values_list("student_id", flat=True).distinct()


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def compiler_history(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to view SQL history.", 401)
    from .models import CompilerQueryHistory, Enrollment, PortalUser
    from .portal_views import _active_portal_role
    role = _active_portal_role(request)
    if role == "STUDENT":
        if request.method == "DELETE":
            return json_error("Students cannot clear SQL history.", 403)
        records = CompilerQueryHistory.objects.filter(student=request.user, is_active=True)
        students = [request.user]
    elif role == "TEACHER" and request.GET.get("own") == "1":
        records = CompilerQueryHistory.objects.filter(student=request.user)
        students_query = PortalUser.objects.filter(pk__in=_teacher_student_ids(request.user), role=PortalUser.Role.STUDENT)
        student_status = request.GET.get("studentStatus", "all")
        if student_status == "active": students_query = students_query.filter(is_active=True)
        elif student_status == "inactive": students_query = students_query.filter(is_active=False)
        students = list(students_query.order_by("first_name", "last_name", "username"))
    elif role == "TEACHER":
        assigned = _teacher_student_ids(request.user)
        student_status = request.GET.get("studentStatus", "all")
        students_query = PortalUser.objects.filter(pk__in=assigned, role=PortalUser.Role.STUDENT)
        if student_status == "active": students_query = students_query.filter(is_active=True)
        elif student_status == "inactive": students_query = students_query.filter(is_active=False)
        students = list(students_query.order_by("first_name", "last_name", "username"))
        selected = request.GET.get("student", "").strip()
        if selected:
            try: selected_id = int(selected)
            except ValueError: return json_error("Invalid student selection.")
            if not any(student.pk == selected_id for student in students): return json_error("Student is not available in your activity scope.", 403)
            student_ids = [selected_id]
        else:
            student_ids = [student.pk for student in students]
        records = CompilerQueryHistory.objects.filter(student_id__in=student_ids)
    else:
        return json_error("Only students and teachers can access SQL history.", 403)
    if request.method == "DELETE":
        return json_error("Select a student to clear their SQL history.", 400)
    today_counts = records.filter(executed_at__date=timezone.localdate()).aggregate(
        successful=Count("id", filter=Q(status="success")),
        failed=Count("id", filter=Q(status="error")),
    )
    # Apply filters and pagination in SQL so a history page never loads every
    # statement into Python.
    source = request.GET.get("source", "all")
    status = request.GET.get("status", "all")
    search = request.GET.get("search", "").strip()[:200]
    if source in {"PL/SQL", "SQL*Plus"}: records = records.filter(source=source)
    if status in {"success", "error"}: records = records.filter(status=status)
    if search:
        records = records.filter(
            Q(sql__icontains=search) | Q(student__first_name__icontains=search)
            | Q(student__middle_name__icontains=search) | Q(student__last_name__icontains=search)
            | Q(student__student_number__icontains=search) | Q(student__username__icontains=search)
        )
    for parameter, lookup in (("from", "gte"), ("to", "lte")):
        raw_date = request.GET.get(parameter, "")
        if raw_date:
            try: parsed_date = date.fromisoformat(raw_date)
            except ValueError: return json_error(f"Invalid {parameter} date.", 400)
            records = records.filter(**{f"executed_at__date__{lookup}": parsed_date})
    sort_fields = {
        "executedAt": "executed_at", "studentName": "student__first_name",
        "source": "source", "status": "status", "durationMs": "duration_ms",
        "rows": "row_count", "sql": "sql",
    }
    sort_key = request.GET.get("sort", "executedAt")
    if sort_key not in sort_fields: sort_key = "executedAt"
    direction = "" if request.GET.get("direction") == "asc" else "-"
    try: page = max(1, int(request.GET.get("page", "1")))
    except ValueError: page = 1
    try: page_size = min(1000, max(1, int(request.GET.get("pageSize", "1000"))))
    except ValueError: page_size = 1000
    total = records.count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    offset = (page - 1) * page_size
    from django.db.models import OuterRef
    from bisect import bisect_right
    from .models import CompilerClientActivityEvent, CompilerQueryHistory
    previous = CompilerQueryHistory.objects.filter(student_id=OuterRef("student_id"), executed_at__lt=OuterRef("executed_at")).order_by("-executed_at").values("executed_at")[:1]
    records = list(records.select_related("student").annotate(previous_execution=Subquery(previous)).order_by(f"{direction}{sort_fields[sort_key]}", f"{direction}executed_at", f"{direction}id")[offset:offset + page_size])
    focus_by_student = {}
    if records:
        lower = min(record.previous_execution or record.executed_at.replace(hour=0, minute=0, second=0, microsecond=0) for record in records)
        upper = max(record.executed_at for record in records)
        focus_rows = CompilerClientActivityEvent.objects.filter(student_id__in={record.student_id for record in records}, created_at__gt=lower, created_at__lte=upper, kind__in=[CompilerClientActivityEvent.Kind.PAGE_HIDDEN, CompilerClientActivityEvent.Kind.WINDOW_BLUR]).order_by("created_at").values_list("student_id", "created_at")
        for student_id, created_at in focus_rows:
            focus_by_student.setdefault(student_id, []).append(created_at)
    records_payload = []
    for record in records:
        previous_execution = record.previous_execution or record.executed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        focus_times = focus_by_student.get(record.student_id, [])
        focus_events = bisect_right(focus_times, record.executed_at) - bisect_right(focus_times, previous_execution)
        item = {"id": record.pk, "studentId": record.student_id,
                "studentName": record.student.get_full_name() or record.student.username,
                "studentNumber": record.student.student_number, "sql": record.sql,
                "source": record.source, "status": record.status,
                "durationMs": record.duration_ms, "rows": record.row_count,
                "message": record.message, "isActive": record.is_active,
                "inputMethod": record.input_method,
                "inputSourceMap": record.input_source_map,
                "focusLossCount": focus_events,
                "executedAt": record.executed_at.isoformat()}
        if role == "TEACHER":
            item.update({"typedChars": record.typed_chars, "pastedChars": record.pasted_chars,
                         "pasteCount": record.paste_count, "editsAfterPaste": record.edits_after_paste})
            item["pastedTexts"] = record.pasted_texts
        records_payload.append(item)
    return JsonResponse({
        "classes": _history_classes(request.user) if role == "TEACHER" else [],
        "students": [{"id": student.pk, "name": student.get_full_name() or student.username,
                      "number": student.student_number, "isActive": student.is_active} for student in students],
        "studentScope": "all" if request.user.can_view_all_compiler_activity else "assigned",
        "todayCounts": {"successful": today_counts["successful"], "failed": today_counts["failed"]},
        "records": records_payload,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "pages": pages},
    })


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def compiler_history_item(request, history_id):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to manage SQL history.", 401)
    from .models import CompilerQueryHistory
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != "TEACHER": return json_error("Only teachers can manage student SQL history.", 403)
    entry = CompilerQueryHistory.objects.filter(pk=history_id, student_id__in=_teacher_student_ids(request.user)).first()
    if not entry: return json_error("History entry not found in your activity scope.", 404)
    if request.method == "DELETE":
        entry.delete()
        return JsonResponse({"deleted": True})
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 400)
    if not isinstance(payload.get("isActive"), bool): return json_error("Choose active or inactive status.")
    entry.is_active = payload["isActive"]
    entry.save(update_fields=["is_active"])
    return JsonResponse({"id": entry.pk, "isActive": entry.is_active})


@csrf_exempt
@require_http_methods(["DELETE"])
def compiler_history_clear_student(request, student_id):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to manage SQL history.", 401)
    from .models import CompilerQueryHistory
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != "TEACHER": return json_error("Only teachers can clear student SQL history.", 403)
    if student_id not in _teacher_student_ids(request.user):
        return json_error("Student is not available in your activity scope.", 403)
    deleted, _ = CompilerQueryHistory.objects.filter(student_id=student_id).delete()
    return JsonResponse({"cleared": True, "deleted": deleted})


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def compiler_settings(request):
    if not same_origin(request): return json_error('Cross-origin API requests are blocked.', 403)
    if not request.user.is_authenticated: return json_error('Sign in to view compiler settings.', 401)
    from .models import (CompilerTabAppearanceOverride, PortalSetting, PortalUser, TeacherActivityPreference)
    from .portal_views import _active_portal_role
    role = _active_portal_role(request)
    is_admin = role == 'ADMIN' or request.user.is_superuser
    can_manage = is_admin or role == 'TEACHER'
    default_tabs = {
        'sqlplus': {'label': 'SQLCL', 'visible': True, 'order': 1},
        'compiler': {'label': 'SQL WorkSheet', 'visible': False, 'order': 2},
    }
    setting = PortalSetting.objects.filter(pk=1).first()
    global_tabs = setting.compiler_tab_appearance if setting and isinstance(setting.compiler_tab_appearance, dict) else default_tabs
    if set(global_tabs) != {'sqlplus', 'compiler'}: global_tabs = default_tabs

    if is_admin:
        allowed_students = PortalUser.objects.filter(role=PortalUser.Role.STUDENT)
    elif role == 'TEACHER':
        if request.user.can_view_all_compiler_activity:
            allowed_students = PortalUser.objects.filter(role=PortalUser.Role.STUDENT)
        else:
            allowed_students = PortalUser.objects.filter(pk__in=_teacher_student_ids(request.user), role=PortalUser.Role.STUDENT)
    else:
        allowed_students = PortalUser.objects.none()

    if request.method == 'POST':
        if not can_manage: return json_error('Only admins and teachers can change shared compiler settings.', 403)
        try:
            payload = read_json(request)
        except ValueError as exc: return json_error(str(exc))
        if 'independentCompilerSessions' in payload:
            if role != 'TEACHER':
                return json_error('Only teachers can change independent compiler session access.', 403)
            enabled = payload.get('independentCompilerSessions')
            if type(enabled) is not bool:
                return json_error('Choose whether independent Oracle sessions should be enabled.')
            preference, _ = TeacherActivityPreference.objects.get_or_create(teacher=request.user)
            preference.independent_compiler_sessions_enabled = enabled
            preference.save(update_fields=('independent_compiler_sessions_enabled', 'updated_at'))
            student_ids = list(allowed_students.values_list('pk', flat=True))
            from django.core.cache import cache
            for student_id in student_ids:
                cache.delete(f'independent-compiler-session:{student_id}')
            if not enabled:
                student_id_set = set(student_ids)
                for state in list(_oracle_session_tokens.values()):
                    if state.get('user_id') in student_id_set and not _independent_compiler_session_allowed(state.get('user_id')):
                        for active_token, current in list(_oracle_session_tokens.items()):
                            if current is state: _oracle_session_tokens.pop(active_token, None)
                        state['independent_enabled'] = False
                        state.pop('independent_token', None)
            return JsonResponse({'ok': True, 'independentCompilerSessions': enabled,
                                 'affectedCount': len(student_ids)})
        if 'bufferEntries' in payload:
            value = payload.get('bufferEntries')
            if type(value) is not int or not 100 <= value <= 50000:
                return json_error('Buffer size must be a whole number from 100 to 50000.')
            PortalSetting.objects.update_or_create(pk=1, defaults={'sqlplus_buffer_entries': value})
            if 'tabAppearance' not in payload:
                setting = PortalSetting.objects.filter(pk=1).first()
                tabs = setting.compiler_tab_appearance if setting and isinstance(setting.compiler_tab_appearance, dict) else default_tabs
                return JsonResponse({'bufferEntries': value, 'canManageDefault': can_manage, 'tabAppearance': tabs})
        tabs = payload.get('tabAppearance')
        if not isinstance(tabs, dict): return json_error('Tab settings are invalid.')
        normalized_tabs = {}
        for key in ('sqlplus', 'compiler'):
            item = tabs.get(key)
            if not isinstance(item, dict): return json_error('Both compiler tabs need valid settings.')
            label, visible, order = item.get('label'), item.get('visible'), item.get('order')
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 30:
                return json_error('Tab names must contain 1 to 30 characters.')
            if type(visible) is not bool or type(order) is not int or order not in (1, 2):
                return json_error('Tab visibility or order is invalid.')
            normalized_tabs[key] = {'label': label.strip(), 'visible': visible, 'order': order}
        if not any(tab['visible'] for tab in normalized_tabs.values()):
            return json_error('At least one compiler tab must remain visible.')
        if normalized_tabs['sqlplus']['order'] == normalized_tabs['compiler']['order']:
            return json_error('Choose a different position for each tab.')

        target = payload.get('target') or {}
        scope = target.get('scope') if isinstance(target, dict) else None
        student_ids = set()
        if scope == 'all':
            student_ids = set(allowed_students.values_list('pk', flat=True))
        elif scope == 'students':
            raw_ids = target.get('studentIds', [])
            if not isinstance(raw_ids, list) or not raw_ids:
                return json_error('Select at least one student.')
            try: requested_ids = {int(value) for value in raw_ids}
            except (TypeError, ValueError): return json_error('Student selection is invalid.')
            allowed_ids = set(allowed_students.filter(pk__in=requested_ids).values_list('pk', flat=True))
            if allowed_ids != requested_ids: return json_error('One or more selected students are outside your teaching scope.', 403)
            student_ids = allowed_ids
        else:
            return json_error('Choose all students or selected students.')
        if not student_ids: return json_error('No students were found in that selection.')
        if scope == 'all' and is_admin:
            PortalSetting.objects.update_or_create(pk=1, defaults={'compiler_tab_appearance': normalized_tabs})
        for student_id in student_ids:
            CompilerTabAppearanceOverride.objects.update_or_create(
                student_id=student_id,
                defaults={'appearance': normalized_tabs, 'updated_by': request.user},
            )
        return JsonResponse({'ok': True, 'tabAppearance': normalized_tabs, 'affectedCount': len(student_ids)})

    if role == 'STUDENT':
        override = CompilerTabAppearanceOverride.objects.filter(student=request.user).first()
        effective_tabs = override.appearance if override else global_tabs
        return JsonResponse({'bufferEntries': setting.sqlplus_buffer_entries if setting else 5000,
                             'canManageDefault': False, 'canManageTabs': False,
                             'tabAppearance': effective_tabs, 'effectiveTabs': effective_tabs})

    students = [{
        'id': student.pk,
        'name': student.get_full_name().strip() or student.username,
        'identifier': student.student_number or student.username,
    } for student in allowed_students.order_by('first_name', 'last_name', 'username')]
    teacher_preference = TeacherActivityPreference.objects.filter(teacher=request.user).first() if role == 'TEACHER' else None
    return JsonResponse({'bufferEntries': setting.sqlplus_buffer_entries if setting else 5000,
                         'canManageDefault': can_manage, 'canManageTabs': can_manage,
                         'canManageIndependentSessions': role == 'TEACHER',
                         'independentCompilerSessions': bool(teacher_preference and teacher_preference.independent_compiler_sessions_enabled),
                         'tabAppearance': global_tabs, 'effectiveTabs': global_tabs,
                         'students': students})


@csrf_exempt
@require_http_methods(["GET"])
def compiler_shared_code(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to view code shared with you.", 401)
    from .models import Enrollment, SharedCompilerCode
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != "STUDENT": return json_error("Shared student code is available to student accounts.", 403)
    enrolled_class_ids = Enrollment.objects.filter(student=request.user, active=True).values("class_section_id")
    records = SharedCompilerCode.objects.filter(published=True).filter(
        Q(all_students=True)
        | Q(student=request.user)
        | Q(class_section_id__in=Subquery(enrolled_class_ids))
        | Q(lab__class_section_id__in=Subquery(enrolled_class_ids))
    ).select_related("created_by").order_by("-created_at")[:50]
    return JsonResponse({"items": [{
        "id": item.pk,
        "title": item.title,
        "editorKind": item.editor_kind,
        "editorLabel": item.get_editor_kind_display(),
        "code": item.code,
        "teacher": item.created_by.get_full_name() or item.created_by.username,
        "createdAt": item.created_at.isoformat(),
    } for item in records]})


def _history_classes(user):
    from .models import ClassSection
    classes = ClassSection.objects.select_related('course')
    if not user.can_view_all_compiler_activity:
        classes = classes.filter(teacher_assignments__teacher=user)
    return [{"id": item.pk, "name": str(item)} for item in classes.distinct().order_by('name', 'section')]


@csrf_exempt
@require_http_methods(["DELETE"])
def compiler_history_clear_scope(request):
    if not same_origin(request): return json_error('Cross-origin API requests are blocked.', 403)
    if not request.user.is_authenticated: return json_error('Sign in to manage SQL history.', 401)
    from .models import CompilerQueryHistory, Enrollment
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != 'TEACHER': return json_error('Only teachers can clear student SQL history.', 403)
    try: payload = read_json(request)
    except ValueError as exc: return json_error(str(exc))
    student_ids = _teacher_student_ids(request.user)
    scope = payload.get('scope')
    if scope == 'student':
        try: selected = int(payload.get('studentId'))
        except (ValueError, TypeError): return json_error('Choose a student.')
        if selected not in student_ids: return json_error('Student is outside your activity scope.', 403)
        student_ids = [selected]
    elif scope == 'class':
        try: selected = int(payload.get('classId'))
        except (ValueError, TypeError): return json_error('Choose a class.')
        if selected not in [item['id'] for item in _history_classes(request.user)]:
            return json_error('Class is outside your activity scope.', 403)
        student_ids = Enrollment.objects.filter(class_section_id=selected, student_id__in=student_ids).values_list('student_id', flat=True)
    elif scope != 'all': return json_error('Choose student, class, or all students.')
    deleted, _ = CompilerQueryHistory.objects.filter(student_id__in=student_ids).delete()
    return JsonResponse({'cleared': True, 'deleted': deleted})


def _editor_share_payload(share, student=None):
    from django.core.cache import cache
    viewers = cache.get(f"remote-sync:{share.student_id}", {})
    active_teachers = [value[0] for value in viewers.values() if value[1] > timezone.now().timestamp() - 15]
    requesting_teacher = share.edit_permission_requested_by
    return {
        "studentId": share.student_id,
        "syncConnected": bool(active_teachers),
        "syncTeachers": active_teachers,
        "studentName": (student.get_full_name() or student.username) if student else "",
        "mode": share.access_mode,
        "editorText": share.editor_text,
        "sqlplusText": share.sqlplus_text,
        "revision": share.revision,
        "statementSnapshotReady": share.statement_snapshot_ready,
        "editPermissionRequest": ({
            "id": share.edit_permission_requested_at.isoformat(),
            "teacherName": requesting_teacher.get_full_name() or requesting_teacher.username if requesting_teacher else "Your teacher",
        } if share.edit_permission_requested_at else None),
        "editPermissionRequestedBy": share.edit_permission_requested_by_id,
        "updatedAt": share.updated_at.isoformat(),
    }


def _reset_teacher_editor_session_access(student_id):
    """Revoke session-scoped teacher editing when a student session ends."""
    from .models import CompilerEditorShare
    return CompilerEditorShare.objects.filter(
        student_id=student_id,
        access_mode=CompilerEditorShare.AccessMode.SESSION,
    ).update(
        access_mode=CompilerEditorShare.AccessMode.PRIVATE,
        session_token="",
        statement_snapshot_ready=False,
        edit_permission_requested_at=None,
        edit_permission_requested_by=None,
        updated_at=timezone.now(),
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def compiler_editor_share(request, student_id=None):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to access shared editors.", 401)
    from .models import CompilerEditorShare, PortalUser
    from .portal_views import _active_portal_role
    role = _active_portal_role(request)
    CompilerEditorShare.objects.filter(
        student_id=request.user.pk if role == 'STUDENT' else student_id,
        access_mode=CompilerEditorShare.AccessMode.STATEMENT,
        updated_at__lt=timezone.now() - timedelta(minutes=1),
    ).update(access_mode=CompilerEditorShare.AccessMode.PRIVATE, statement_snapshot_ready=False)
    CompilerEditorShare.objects.filter(
        student_id=request.user.pk if role == 'STUDENT' else student_id,
        access_mode=CompilerEditorShare.AccessMode.SESSION,
        updated_at__lt=timezone.now() - timedelta(minutes=1),
    ).update(
        access_mode=CompilerEditorShare.AccessMode.PRIVATE,
        session_token="",
        statement_snapshot_ready=False,
        edit_permission_requested_at=None,
        edit_permission_requested_by=None,
        updated_at=timezone.now(),
    )
    if request.method == "GET":
        if role == "STUDENT" and student_id is None:
            share, _ = CompilerEditorShare.objects.get_or_create(student=request.user)
            return JsonResponse(_editor_share_payload(share, request.user))
        if role != "TEACHER" or student_id is None:
            return json_error("Only students can manage sharing and teachers can view students in their activity scope.", 403)
        student = PortalUser.objects.filter(pk__in=_teacher_student_ids(request.user), role=PortalUser.Role.STUDENT).filter(pk=student_id).first()
        if not student: return json_error("Student is not available in your activity scope.", 403)
        share, _ = CompilerEditorShare.objects.get_or_create(student=student)
        from django.core.cache import cache
        viewers = cache.get(f"remote-sync:{student.pk}", {})
        viewers[str(request.user.pk)] = (request.user.get_full_name() or request.user.username, timezone.now().timestamp())
        cache.set(f"remote-sync:{student.pk}", viewers, 30)
        return JsonResponse(_editor_share_payload(share, student))
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 400)
    if role == "STUDENT" and student_id is None:
        share, _ = CompilerEditorShare.objects.get_or_create(student=request.user)
        if payload.get("action") == "sync_view_snapshot":
            editor_text, sqlplus_text = payload.get("editorText"), payload.get("sqlplusText")
            if not isinstance(editor_text, str) or not isinstance(sqlplus_text, str):
                return json_error("Both editor contents are required.")
            if "revision" in payload and payload.get("revision") != share.revision:
                return json_error("The shared editor changed. Reload it before syncing your changes.", 409)
            editor_text, sqlplus_text = editor_text[:MAX_BODY], sqlplus_text[:MAX_BODY]
            update_fields = [] if share.access_mode == CompilerEditorShare.AccessMode.STATEMENT else ["updated_at"]
            if editor_text != share.editor_text or sqlplus_text != share.sqlplus_text:
                share.editor_text, share.sqlplus_text = editor_text, sqlplus_text
                share.revision += 1
                update_fields.extend(("editor_text", "sqlplus_text", "revision"))
            if update_fields:
                share.save(update_fields=update_fields)
            return JsonResponse(_editor_share_payload(share, request.user))
        if payload.get("action") == "respond_edit_permission":
            request_id = share.edit_permission_requested_at.isoformat() if share.edit_permission_requested_at else ""
            if not request_id or payload.get("requestId") != request_id:
                return json_error("This edit request is no longer active.", 409)
            approved = payload.get("approved") is True
            if approved:
                token = str(payload.get("sessionToken") or "")
                editor_text, sqlplus_text = payload.get("editorText"), payload.get("sqlplusText")
                if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token): return json_error("Invalid editor session token.")
                share.access_mode = CompilerEditorShare.AccessMode.SESSION
                share.session_token = token
                share.statement_snapshot_ready = False
                if isinstance(editor_text, str) and isinstance(sqlplus_text, str):
                    share.editor_text = editor_text[:MAX_BODY]
                    share.sqlplus_text = sqlplus_text[:MAX_BODY]
                share.revision += 1
            elif share.access_mode == CompilerEditorShare.AccessMode.SESSION and timezone.now() - share.updated_at > timedelta(minutes=1):
                share.access_mode = CompilerEditorShare.AccessMode.PRIVATE
                share.session_token = ""
            share.edit_permission_requested_at = None
            share.edit_permission_requested_by = None
            share.save(update_fields=("access_mode", "session_token", "statement_snapshot_ready", "editor_text", "sqlplus_text", "revision", "edit_permission_requested_at", "edit_permission_requested_by", "updated_at"))
            return JsonResponse(_editor_share_payload(share, request.user))
        mode = payload.get("mode")
        allowed_modes = {CompilerEditorShare.AccessMode.PRIVATE, CompilerEditorShare.AccessMode.SESSION, CompilerEditorShare.AccessMode.ALWAYS}
        if mode not in allowed_modes: return json_error("Choose a valid teacher editor access setting.")
        if "revision" in payload and payload.get("revision") != share.revision:
            return json_error("The shared editor changed. Reload it before syncing your changes.", 409)
        changed_fields = ["access_mode", "session_token", "statement_snapshot_ready", "revision", "updated_at"]
        if mode != share.access_mode:
            share.statement_snapshot_ready = False
        share.access_mode = mode
        if mode != CompilerEditorShare.AccessMode.PRIVATE:
            share.edit_permission_requested_at = None
            share.edit_permission_requested_by = None
            changed_fields.extend(("edit_permission_requested_at", "edit_permission_requested_by"))
        if mode == CompilerEditorShare.AccessMode.PRIVATE:
            share.session_token = ""
            share.statement_snapshot_ready = False
            editor_text, sqlplus_text = payload.get("editorText"), payload.get("sqlplusText")
            if isinstance(editor_text, str) and isinstance(sqlplus_text, str):
                editor_text, sqlplus_text = editor_text[:MAX_BODY], sqlplus_text[:MAX_BODY]
                if editor_text != share.editor_text or sqlplus_text != share.sqlplus_text:
                    share.editor_text, share.sqlplus_text = editor_text, sqlplus_text
                    share.revision += 1
                    changed_fields.extend(("editor_text", "sqlplus_text", "revision"))
        elif mode == CompilerEditorShare.AccessMode.SESSION:
            token = str(payload.get("sessionToken") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token): return json_error("Invalid editor session token.")
            share.session_token = token
            if isinstance(payload.get("editorText"), str) and isinstance(payload.get("sqlplusText"), str):
                editor_text, sqlplus_text = payload["editorText"][:MAX_BODY], payload["sqlplusText"][:MAX_BODY]
                if editor_text != share.editor_text or sqlplus_text != share.sqlplus_text:
                    share.editor_text, share.sqlplus_text = editor_text, sqlplus_text
                    share.revision += 1
                    changed_fields.extend(["editor_text", "sqlplus_text", "revision"])
        elif mode == CompilerEditorShare.AccessMode.ALWAYS:
            share.session_token = ""
            if isinstance(payload.get("editorText"), str) and isinstance(payload.get("sqlplusText"), str):
                editor_text, sqlplus_text = payload["editorText"][:MAX_BODY], payload["sqlplusText"][:MAX_BODY]
                if editor_text != share.editor_text or sqlplus_text != share.sqlplus_text:
                    share.editor_text, share.sqlplus_text = editor_text, sqlplus_text
                    share.revision += 1
                    changed_fields.extend(["editor_text", "sqlplus_text", "revision"])
        elif mode == CompilerEditorShare.AccessMode.STATEMENT:
            share.session_token = ""
            if payload.get("statementExecution") and not share.statement_snapshot_ready:
                if not isinstance(payload.get("editorText"), str) or not isinstance(payload.get("sqlplusText"), str):
                    return json_error("Editor text is required to share this statement.")
                share.editor_text = payload["editorText"][:MAX_BODY]
                share.sqlplus_text = payload["sqlplusText"][:MAX_BODY]
                share.statement_snapshot_ready = True
                share.revision += 1
                changed_fields.extend(["editor_text", "sqlplus_text"])
        share.save(update_fields=list(set(changed_fields)))
        return JsonResponse(_editor_share_payload(share, request.user))
    if role == "TEACHER" and student_id is not None:
        student = PortalUser.objects.filter(pk__in=_teacher_student_ids(request.user), role=PortalUser.Role.STUDENT).filter(pk=student_id).first()
        if not student: return json_error("Student is not available in your activity scope.", 403)
        share, _ = CompilerEditorShare.objects.get_or_create(student=student)
        if payload.get("action") == "disconnect_sync":
            from django.core.cache import cache
            viewers = cache.get(f"remote-sync:{student.pk}", {})
            viewers.pop(str(request.user.pk), None)
            cache.set(f"remote-sync:{student.pk}", viewers, 30)
            from .models import CompilerRemoteTerminalCommand
            CompilerRemoteTerminalCommand.objects.filter(student=student, teacher=request.user, status="queued").update(status="expired", completed_at=timezone.now(), error="Teacher disconnected remote sync.")
            return JsonResponse({"disconnected": True})
        if payload.get("action") == "request_edit_permission":
            age = timezone.now() - share.updated_at
            has_edit_permission = share.access_mode == CompilerEditorShare.AccessMode.ALWAYS or (share.access_mode == CompilerEditorShare.AccessMode.SESSION and age <= timedelta(minutes=1)) or (share.access_mode == CompilerEditorShare.AccessMode.STATEMENT and share.statement_snapshot_ready and age <= timedelta(minutes=1))
            if has_edit_permission:
                return json_error("This student has already allowed editor access.", 409)
            share.edit_permission_requested_at = timezone.now()
            share.edit_permission_requested_by = request.user
            share.save(update_fields=("edit_permission_requested_at", "edit_permission_requested_by"))
            return JsonResponse(_editor_share_payload(share, student))
        has_edit_permission = share.access_mode in {CompilerEditorShare.AccessMode.SESSION, CompilerEditorShare.AccessMode.ALWAYS} or (share.access_mode == CompilerEditorShare.AccessMode.STATEMENT and share.statement_snapshot_ready)
        if not has_edit_permission:
            return json_error("Student permission is required to edit this editor.", 403)
        if share.access_mode == CompilerEditorShare.AccessMode.SESSION and timezone.now() - share.updated_at > timedelta(minutes=1):
            return json_error("This student editor session is no longer active.", 403)
        if share.access_mode == CompilerEditorShare.AccessMode.STATEMENT and not share.statement_snapshot_ready:
            return json_error("The student has not shared a statement yet.", 403)
        editor_text, sqlplus_text = payload.get("editorText"), payload.get("sqlplusText")
        if not isinstance(editor_text, str) or not isinstance(sqlplus_text, str): return json_error("Both editor contents are required.")
        if payload.get("revision") != share.revision: return json_error("The shared editor changed. Reload it before saving.", 409)
        share.editor_text = editor_text[:MAX_BODY]
        share.sqlplus_text = sqlplus_text[:MAX_BODY]
        share.revision += 1
        if share.access_mode == CompilerEditorShare.AccessMode.STATEMENT:
            share.access_mode = CompilerEditorShare.AccessMode.PRIVATE
            share.statement_snapshot_ready = False
        share.save(update_fields=["editor_text", "sqlplus_text", "revision", "access_mode", "statement_snapshot_ready", "updated_at"])
        return JsonResponse(_editor_share_payload(share, student))
    return json_error("You are not allowed to change this editor sharing setting.", 403)


def _remote_terminal_teacher_access(request, student_id, *, require_edit=True):
    from .models import CompilerEditorShare, PortalUser
    from .portal_views import _active_portal_role
    user = request.user
    if _active_portal_role(request) != "TEACHER":
        return None, None, json_error("Only teachers can send remote terminal commands.", 403)
    student = PortalUser.objects.filter(
        pk__in=_teacher_student_ids(user), role=PortalUser.Role.STUDENT, pk=student_id,
    ).first()
    if not student:
        return None, None, json_error("Student is not available in your teaching scope.", 403)
    share = CompilerEditorShare.objects.filter(student=student).first()
    now = timezone.now()
    allowed = bool(share and (
        share.access_mode == CompilerEditorShare.AccessMode.ALWAYS
        or (share.access_mode == CompilerEditorShare.AccessMode.SESSION and now - share.updated_at <= timedelta(minutes=1))
        or (share.access_mode == CompilerEditorShare.AccessMode.STATEMENT and share.statement_snapshot_ready and now - share.updated_at <= timedelta(minutes=1))
    ))
    if require_edit and not allowed:
        return None, None, json_error("Current student permission to edit is required to execute a remote command.", 403)
    return student, share, None


def _expire_abandoned_remote_commands(student):
    from .models import CompilerRemoteTerminalCommand
    CompilerRemoteTerminalCommand.objects.filter(
        student=student, status=CompilerRemoteTerminalCommand.Status.RUNNING,
        started_at__lt=timezone.now() - timedelta(minutes=15),
    ).update(
        status=CompilerRemoteTerminalCommand.Status.EXPIRED, completed_at=timezone.now(),
        error="The student compiler did not return a result. This command was not replayed; check the student terminal before submitting it again.",
    )


def _remote_terminal_payload(command):
    if not command:
        return {"command": None}
    return {"command": {
        "id": str(command.id), "text": command.command, "status": command.status,
        "editor": command.editor_kind, "script": command.run_script,
        "output": command.output, "error": command.error,
        "executed": command.execution_finished,
        "requestedAt": command.requested_at.isoformat(),
        "startedAt": command.started_at.isoformat() if command.started_at else None,
        "completedAt": command.completed_at.isoformat() if command.completed_at else None,
    }}


@csrf_exempt
@require_http_methods(["GET", "POST"])
def compiler_remote_terminal(request, student_id):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to use the remote terminal.", 401)
    from .models import CompilerRemoteTerminalCommand
    student, share, error = _remote_terminal_teacher_access(request, student_id, require_edit=request.method == "POST")
    if error: return error
    _expire_abandoned_remote_commands(student)
    if request.method == "GET":
        CompilerRemoteTerminalCommand.objects.filter(
            student=student, teacher=request.user, status=CompilerRemoteTerminalCommand.Status.QUEUED,
            requested_at__lt=timezone.now() - timedelta(seconds=60),
        ).update(status=CompilerRemoteTerminalCommand.Status.EXPIRED, completed_at=timezone.now(), error="Student terminal was unavailable for 60 seconds.")
        latest = CompilerRemoteTerminalCommand.objects.filter(student=student, teacher=request.user).first()
        return JsonResponse(_remote_terminal_payload(latest))
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 400)
    command_text = payload.get("command")
    editor_kind = payload.get("editor", "sqlplus")
    run_script = payload.get("script") is True
    try: request_id = uuid.UUID(str(payload.get("requestId", "")))
    except (ValueError, TypeError, AttributeError): return json_error("Invalid remote command request ID.", 400)
    if not isinstance(command_text, str) or not command_text.strip() or len(command_text) > 20000:
        return json_error("Enter a SQLCL command under 20,000 characters.", 400)
    if editor_kind not in {"sqlplus", "worksheet"} or (run_script and editor_kind != "worksheet"):
        return json_error("Choose SQLCL or SQL WorkSheet and a valid execution mode.", 400)
    now = timezone.now()
    with transaction.atomic():
        existing = CompilerRemoteTerminalCommand.objects.filter(teacher=request.user, request_id=request_id).first()
        if existing:
            if existing.student_id != student.id: return json_error("Request ID belongs to a different student.", 409)
            return JsonResponse(_remote_terminal_payload(existing))
        CompilerRemoteTerminalCommand.objects.filter(
            student=student, status=CompilerRemoteTerminalCommand.Status.QUEUED,
            requested_at__lt=now - timedelta(seconds=60),
        ).update(status=CompilerRemoteTerminalCommand.Status.EXPIRED, completed_at=now, error="Student terminal was unavailable for 60 seconds.")
        if CompilerRemoteTerminalCommand.objects.filter(student=student, status__in=(CompilerRemoteTerminalCommand.Status.QUEUED, CompilerRemoteTerminalCommand.Status.RUNNING)).exists():
            return json_error("A remote command is already waiting or running for this student.", 409)
        command = CompilerRemoteTerminalCommand.objects.create(
            student=student, teacher=request.user, request_id=request_id, command=command_text.strip(),
            editor_kind=editor_kind, run_script=run_script,
        )
        if share.access_mode == "statement":
            share.access_mode = "private"
            share.statement_snapshot_ready = False
            share.save(update_fields=("access_mode", "statement_snapshot_ready", "updated_at"))
    return JsonResponse(_remote_terminal_payload(command), status=202)


@csrf_exempt
@require_http_methods(["GET"])
def compiler_remote_terminal_pending(request):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to receive remote commands.", 401)
    from .models import CompilerRemoteTerminalCommand
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != "STUDENT": return json_error("Only students can receive remote commands.", 403)
    _expire_abandoned_remote_commands(request.user)
    now = timezone.now()
    with transaction.atomic():
        CompilerRemoteTerminalCommand.objects.filter(
            student=request.user, status=CompilerRemoteTerminalCommand.Status.QUEUED,
            requested_at__lt=now - timedelta(seconds=60),
        ).update(status=CompilerRemoteTerminalCommand.Status.EXPIRED, completed_at=now, error="Student terminal was unavailable for 60 seconds.")
        command = CompilerRemoteTerminalCommand.objects.filter(
            student=request.user, status=CompilerRemoteTerminalCommand.Status.QUEUED,
        ).order_by("requested_at").first()
        if command and CompilerRemoteTerminalCommand.objects.filter(
            pk=command.pk, status=CompilerRemoteTerminalCommand.Status.QUEUED,
        ).update(status=CompilerRemoteTerminalCommand.Status.RUNNING, started_at=now):
            command.status = CompilerRemoteTerminalCommand.Status.RUNNING
            command.started_at = now
        else:
            command = None
    return JsonResponse(_remote_terminal_payload(command))


@csrf_exempt
@require_http_methods(["POST"])
def compiler_remote_terminal_complete(request, command_id):
    if not same_origin(request): return json_error("Cross-origin API requests are blocked.", 403)
    if not request.user.is_authenticated: return json_error("Sign in to return the command result.", 401)
    from .models import CompilerRemoteTerminalCommand
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != "STUDENT": return json_error("Only students can return command results.", 403)
    try: payload = read_json(request)
    except ValueError as exc: return json_error(exc, 400)
    with transaction.atomic():
        try:
            command = CompilerRemoteTerminalCommand.objects.select_for_update().get(
                pk=command_id, student=request.user,
            )
        except CompilerRemoteTerminalCommand.DoesNotExist:
            return json_error("This remote command is no longer running.", 409)
        if command.status in (CompilerRemoteTerminalCommand.Status.COMPLETED, CompilerRemoteTerminalCommand.Status.FAILED):
            return JsonResponse(_remote_terminal_payload(command))
        if command.status != CompilerRemoteTerminalCommand.Status.RUNNING:
            return json_error("This remote command is no longer running.", 409)
        command.output = str(payload.get("output") or "")[:20000]
        command.error = str(payload.get("error") or "")[:5000]
        command.execution_finished = payload.get("executed") is True
        command.status = CompilerRemoteTerminalCommand.Status.FAILED if command.error else CompilerRemoteTerminalCommand.Status.COMPLETED
        command.completed_at = timezone.now()
        command.save(update_fields=("output", "error", "execution_finished", "status", "completed_at"))
        if command.editor_kind == "sqlplus" and command.execution_finished:
            from .models import CompilerEditorShare
            share = CompilerEditorShare.objects.filter(student=request.user).first()
            if share and share.sqlplus_text.strip() == command.command.strip():
                share.sqlplus_text = ""
                share.revision += 1
                share.save(update_fields=("sqlplus_text", "revision", "updated_at"))
    return JsonResponse(_remote_terminal_payload(command))


@require_http_methods(["GET"])
def list_objects(request):
    session = current_oracle_session(request)
    if not session: return json_error("Connect to Oracle first.", 401)
    settings_data = session["settings"]; connection = None
    default_only = is_default_connection(settings_data["connectString"], settings_data["user"])
    excluded = "('SYS','OUTLN','DBSNMP','APPQOSSYS','AUDSYS','CTXSYS','DBSFWUSER','GSMADMIN_INTERNAL','LBACSYS','MDSYS','OJVMSYS','ORDDATA','ORDSYS','WMSYS','XDB')"
    owner_filter = "owner = USER" if default_only else f"owner NOT IN {excluded}"
    try:
        names = json.loads(request.GET.get("names", "[]"))
        if not isinstance(names, list) or len(names) > 64 or any(not isinstance(name, str) or not name or len(name) > 128 for name in names):
            return json_error("Invalid object refresh names.")
    except (ValueError, TypeError):
        return json_error("Invalid object refresh names.")
    binds = {f"name{i}": name for i, name in enumerate(names)}
    placeholders = ",".join(f":name{i}" for i in range(len(names)))
    object_scope = (f" AND (object_name IN ({placeholders}) OR object_name IN (SELECT index_name FROM all_indexes WHERE table_name IN ({placeholders})))" if names else "")
    constraint_scope = (f" AND (table_name IN ({placeholders}) OR constraint_name IN ({placeholders}))" if names else "")
    try:
        connection = open_oracle_connection(connect_options(settings_data))
        # Dictionary queries can exceed the shorter interactive SQL timeout.
        connection.call_timeout = ORACLE_METADATA_TIMEOUT_MS
        with connection.cursor() as cursor:
            tune_oracle_cursor(cursor)
            cursor.execute(f"SELECT owner, object_name, object_type, status, created FROM all_objects WHERE object_type IN ('TABLE','VIEW','MATERIALIZED VIEW','INDEX','SEQUENCE','PROCEDURE','FUNCTION','PACKAGE','TRIGGER','TYPE','SYNONYM') AND {owner_filter}{object_scope} ORDER BY CASE WHEN owner = USER THEN 0 ELSE 1 END, owner, object_type, object_name FETCH FIRST 4000 ROWS ONLY", binds)
            keys = [column[0] for column in cursor.description]; objects = [dict(zip(keys, row)) for row in cursor.fetchall()]
            cursor.execute(f"SELECT owner, constraint_name AS object_name, 'CONSTRAINT' AS object_type, status, table_name FROM all_constraints WHERE {owner_filter}{constraint_scope} ORDER BY CASE WHEN owner = USER THEN 0 ELSE 1 END, owner, table_name, constraint_name FETCH FIRST 10000 ROWS ONLY", binds)
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
            tune_oracle_cursor(cursor, 100)
            if view == "data":
                cursor.execute("SELECT object_type FROM all_objects WHERE owner=:owner AND object_name=:name AND object_type IN ('TABLE','VIEW','MATERIALIZED VIEW') AND ROWNUM=1", owner=owner, name=name)
                if not cursor.fetchone(): return json_error("Data preview is available for tables and views only.")
                cursor.execute(f'SELECT * FROM "{owner}"."{name}" FETCH FIRST 100 ROWS ONLY')
                cols = [item[0] for item in cursor.description or []]; rows = [[json_value(value) for value in row] for row in cursor.fetchall()]
                return JsonResponse({"owner": owner, "name": name, "view": view, "columns": [{"name": col} for col in cols], "rows": rows, "truncated": len(rows) == 100})
            if view == "constraints":
                connection.call_timeout = ORACLE_METADATA_TIMEOUT_MS
                own_schema = owner == data['user'].upper()
                constraint_source = 'user_constraints' if own_schema else 'all_constraints'
                column_source = 'user_cons_columns' if own_schema else 'all_cons_columns'
                reference_table = """CASE WHEN c.constraint_type='R' THEN
                    (SELECT ref.table_name FROM all_constraints ref
                     WHERE ref.owner=c.r_owner AND ref.constraint_name=c.r_constraint_name)
                    END"""
                if own_schema:
                    reference_table = """CASE WHEN c.constraint_type='R' THEN
                        CASE WHEN c.r_owner=:owner THEN
                            (SELECT ref.table_name FROM user_constraints ref WHERE ref.constraint_name=c.r_constraint_name)
                        ELSE
                            (SELECT ref.table_name FROM all_constraints ref WHERE ref.owner=c.r_owner AND ref.constraint_name=c.r_constraint_name)
                        END END"""
                reference_column = """CASE WHEN c.constraint_type='R' THEN
                    (SELECT refcol.column_name FROM all_cons_columns refcol
                     WHERE refcol.owner=c.r_owner AND refcol.constraint_name=c.r_constraint_name
                       AND refcol.position=cc.position) END"""
                if own_schema:
                    reference_column = """CASE WHEN c.constraint_type='R' THEN
                        CASE WHEN c.r_owner=:owner THEN
                            (SELECT refcol.column_name FROM user_cons_columns refcol
                             WHERE refcol.constraint_name=c.r_constraint_name AND refcol.position=cc.position)
                        ELSE (SELECT refcol.column_name FROM all_cons_columns refcol
                              WHERE refcol.owner=c.r_owner AND refcol.constraint_name=c.r_constraint_name
                                AND refcol.position=cc.position) END END"""
                cursor.execute(f"""
                    SELECT c.constraint_name, c.constraint_type, c.status, c.validated,
                           cc.column_name, cc.position, {reference_table} AS referenced_table_name,
                           c.r_owner AS referenced_owner, {reference_column} AS referenced_column_name
                    FROM {constraint_source} c
                    LEFT JOIN {column_source} cc
                      ON {'' if own_schema else 'cc.owner=c.owner AND '}cc.constraint_name=c.constraint_name AND cc.table_name=c.table_name
                    WHERE {':owner=:owner' if own_schema else 'c.owner=:owner'} AND c.table_name=:name
                    ORDER BY c.constraint_name, cc.position
                """, owner=owner, name=name)
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
