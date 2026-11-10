import json
import re
import time
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from .models import OracleConnectionProfile, ProjectExecution, ProjectRunnerConfiguration
from .sql_lexer import split_statements
from .views import open_oracle_connection, same_origin, connect_options, connect_info
from .project_sqlplus import CLIENT_COMMAND as PROJECT_CLIENT_COMMAND, project_session, close_project_session, validate_sandbox, validate_project_command, SQLPlusInputRequired
from .project_schemas import renew_schema, remove_schema, finish_previous_checks
from .project_output import csv_table, describe_table
from .project_sqlplus import command_text


_TRAILING_CITATION = re.compile(r'(?m)(?<=;)[ \t]*\[cite:[ \t]*\d+(?:[ \t]*,[ \t]*\d+)*[ \t]*\][ \t]*(?=\r?$)', re.I)


def strip_project_citations(source):
    """Ignore pasted reference tags after a SQL terminator, never SQL content."""
    return _TRAILING_CITATION.sub('', source)


def _teacher(request):
    return request.user.is_authenticated and (request.user.is_staff or request.user.has_portal_role("TEACHER"))

def _can_run(request):
    return request.user.is_authenticated and (request.user.is_staff or request.user.has_portal_role("TEACHER") or request.user.has_portal_role("STUDENT"))


DEFAULT_CORRECTION_ATTEMPTS = 3


def correction_attempts_remaining(run):
    used = sum(row.get('type') == 'correction_attempt' and row.get('index') == run.next_index for row in run.results)
    return max(0, DEFAULT_CORRECTION_ATTEMPTS - used)


def _payload(run):
    results = []
    for result in run.results:
        if result.get('type') in {'connection', 'disconnection', 'disconnect', 'correction_attempt'}:
            continue
        item = dict(result)
        index = item.get('index')
        if run.status == 'paused' and item.get('status') == 'error' and index == run.next_index and not item.get('inputs'):
            previous = next((row for row in reversed(run.results) if row.get('index') == index and row.get('status') == 'input' and isinstance(row.get('inputs'), list)), None)
            if previous:
                item['inputs'] = previous['inputs']
        if 'statement' not in item and isinstance(index, int) and 0 <= index < len(run.statements):
            item['statement'] = run.statements[index]
        if not item.get('table') and item.get('output'):
            statement = command_text(item.get('statement', ''))
            if re.match(r'^(?:SELECT|WITH|PRINT)\b', statement, re.I):
                table = csv_table(item['output'])
            elif re.match(r'^DESC\w*\b', statement, re.I):
                table = describe_table(item['output'])
            else:
                table = None
            if table:
                item['table'] = table
        if item.get('inputs') and item.get('statement'):
            from .sql_lexer import trigger_correlation_names
            correlations = trigger_correlation_names(item['statement'])
            item['inputs'] = [value for value in item['inputs'] if not (value.get('kind') == 'bind' and value.get('name', '').upper() in correlations)]
            if correlations and not item['inputs'] and item.get('status') == 'input':
                item['status'] = 'ready'
        results.append(item)
    return {"id": run.pk, "name": run.name, "status": run.status, "nextIndex": run.next_index,
            "correctionAttemptsRemaining": correction_attempts_remaining(run), "correctionAttemptLimit": DEFAULT_CORRECTION_ATTEMPTS,
            "statements": run.statements, "results": results,
            'setupStatementCount':run.setup_statement_count,'activityKind':run.activity_kind,
            'sandboxActive': bool(run.schema_name and not run.schema_removed_at and run.status not in {'completed','cancelled'}),
            'sandboxEnded': bool(run.schema_removed_at or run.status in {'completed','cancelled'})}


def _report_scope(request):
    from .portal_views import _active_portal_role
    from .views import _teacher_student_ids
    from django.db.models import Q
    if request.user.is_staff:
        return ProjectExecution.objects.exclude(activity_kind="grading")
    if _active_portal_role(request) != 'TEACHER':
        return None
    return ProjectExecution.objects.filter(Q(owner=request.user) | Q(owner_id__in=_teacher_student_ids(request.user))).exclude(activity_kind="grading")


@login_required
@never_cache
@require_http_methods(['GET'])
def project_reports(request):
    scope = _report_scope(request)
    if scope is None:
        return JsonResponse({'error': 'Only teachers can view file-checking reports.'}, status=403)
    from django.core.paginator import Paginator
    page = Paginator(scope.select_related('owner').order_by('-created_at'), 25).get_page(request.GET.get('page', 1))
    return JsonResponse({'reports': [{'id': run.pk, 'name': run.name,
        'student': run.owner.get_full_name() or run.owner.username, 'status': run.status,
        'completedStatements': run.next_index, 'totalStatements': len(run.statements),
        'errorCount': sum(result.get('status') == 'error' for result in run.results),
        'createdAt': run.created_at.isoformat()} for run in page],
        'page': page.number, 'pages': page.paginator.num_pages})

@login_required
@never_cache
def project_runner_page(request):
    if not _can_run(request): return JsonResponse({"error": "Sign in to check a project."}, status=403)
    role = request.session.get("active_portal_role", request.user.role)
    if role == "STUDENT" and request.path != "/portal/student/project-check/":
        from django.shortcuts import redirect
        return redirect("/portal/student/project-check/")
    if role == "TEACHER" and request.path == "/portal/student/project-check/":
        from django.shortcuts import redirect
        return redirect("/portal/teacher/project-runs/")
    return render_project_checker(request)


def render_project_checker(request, assessment=None, latest=None):
    role = request.session.get('active_portal_role', request.user.role)
    configuration = ProjectRunnerConfiguration.objects.select_related("sandbox_connection").filter(pk=1).first()
    if assessment is None:
        latest = ProjectExecution.objects.filter(owner=request.user, activity_kind='file').order_by('-created_at', '-pk').first()
    initial = _payload(latest) if latest else None
    if initial:
        initial.update({'source': latest.source_text, 'fileName': latest.original_file_name or latest.name})
    can_configure = request.user.is_staff or role == "ADMIN"
    return render(request, "teacher-project-runner.html", {"sandbox_configuration": configuration, "can_configure_sandbox": can_configure, "is_project_check_page": True, "portal_role": role,
        "profile_role": request.user.get_role_display(), "has_multiple_roles": len(request.user.portal_roles) > 1,
        "global_default_accent": "violet", "can_view_project_reports": request.user.is_staff or role == 'TEACHER',
        'can_manage_teacher_capacity': request.user.is_staff or request.user.has_portal_role('ADMIN'),
        'initial_project_check': initial, 'assessment': assessment})


@login_required
@require_http_methods(["POST"])
def project_runner_capacity(request):
    if not same_origin(request):
        return JsonResponse({'error': 'Cross-origin request blocked.'}, status=403)
    role = request.session.get('active_portal_role', request.user.role)
    if not (request.user.is_staff or role == 'ADMIN' or (role == 'TEACHER' and request.user.has_portal_role('TEACHER'))):
        return JsonResponse({'error': 'Only teachers and administrators can change file-check limits.'}, status=403)
    try:
        data = json.loads(request.body or '{}')
        teacher = data.get('teacherActiveChecks')
        student = data.get('studentActiveChecks')
        if any(type(value) is not int or not 0 <= value <= 35 for value in [teacher, student]) or not 1 <= teacher + student <= 35:
            raise ValueError('Choose whole numbers from 0 to 35 with a combined total from 1 to 35.')
    except (ValueError, TypeError, AttributeError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    from django.db import transaction
    with transaction.atomic():
        try:
            config = ProjectRunnerConfiguration.objects.select_for_update().get(pk=1)
        except ProjectRunnerConfiguration.DoesNotExist:
            config = None
        if config:
            if teacher != config.teacher_active_checks and not (request.user.is_staff or request.user.has_portal_role('ADMIN')):
                return JsonResponse({'error': 'Administrator rights are required to change the teacher file-check limit.'}, status=403)
            config.teacher_active_checks, config.student_active_checks = teacher, student
            config.max_active_checks = teacher + student
            config.save(update_fields=['teacher_active_checks', 'student_active_checks', 'max_active_checks'])
    if config is None:
        return JsonResponse({'error': 'Configure the sandbox before changing its capacity.'}, status=409)
    from .project_queue import start_scheduler, _wake
    start_scheduler()
    _wake.set()
    return JsonResponse({'teacherActiveChecks': teacher, 'studentActiveChecks': student, 'maxActiveChecks': teacher + student})


@login_required
@require_http_methods(["POST"])
def project_runner_configure(request):
    if not same_origin(request): return JsonResponse({"error": "Cross-origin request blocked."}, status=403)
    role = request.session.get("active_portal_role", request.user.role)
    if not (request.user.is_staff or role == "ADMIN"):
        return JsonResponse({"error": "Only a portal administrator can configure the shared project sandbox."}, status=403)
    try:
        payload = json.loads(request.body or "{}")
        name = "Shared project sandbox"
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        connect_string = str(payload.get("connectString") or "").strip()
        current = ProjectRunnerConfiguration.objects.select_related("sandbox_connection").filter(pk=1).first()
        if not username or not connect_string or (not password and not current):
            return JsonResponse({"error": "Enter the sandbox username, password, and Oracle connection string."}, status=400)
        validate_sandbox(username, password or current.sandbox_connection.get_oracle_password(), connect_string)
        profile, _ = OracleConnectionProfile.objects.get_or_create(owner=request.user, name=name,
            defaults={"username": username, "connect_string": connect_string, "password_encrypted": ""})
        profile.username, profile.connect_string = username, connect_string
        changed = ["username", "connect_string"]
        if password:
            profile.set_oracle_password(password)
            changed.append("password_encrypted")
        elif current:
            profile.password_encrypted = current.sandbox_connection.password_encrypted
            changed.append("password_encrypted")
        profile.save(update_fields=changed)
        ProjectRunnerConfiguration.objects.update_or_create(pk=1, defaults={"sandbox_connection": profile})
        return JsonResponse({"configured": True, "username": username, "connectString": connect_string})
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc) or "Invalid sandbox settings."}, status=400)


@login_required
@require_http_methods(["POST"])
def project_start(request):
    if not same_origin(request): return JsonResponse({"error": "Cross-origin request blocked. Refresh the portal page and try again."}, status=403)
    if not _can_run(request): return JsonResponse({"error": "Sign in to check a project."}, status=403)
    try:
        body = json.loads(request.body or "{}")
        source = str(body.get("source") or "")
        configuration = ProjectRunnerConfiguration.objects.select_related("sandbox_connection").get(pk=1)
        profile = configuration.sandbox_connection
    except (ValueError, TypeError, json.JSONDecodeError, ProjectRunnerConfiguration.DoesNotExist):
        return JsonResponse({"error": "The shared project sandbox has not been configured by a portal administrator yet."}, status=503)
    executable_source = strip_project_citations(source)
    statements = [item.strip() for item in split_statements(executable_source, sqlplus=True, client_command=PROJECT_CLIENT_COMMAND) if item.strip()]
    if not statements: return JsonResponse({"error": "No executable statements found."}, status=400)
    try:
        for statement in statements:
            validate_project_command(statement)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    from django.db import transaction
    with transaction.atomic():
        finish_previous_checks(request.user)
        run = ProjectExecution.objects.create(owner=request.user, name=str(body.get("name") or "Project file"),
            original_file_name=str(body.get('fileName') or '')[:255],
            student_id=request.user.pk if request.session.get('active_portal_role', request.user.role) == 'STUDENT' else None,
            queue_role='TEACHER' if request.user.is_staff or request.session.get('active_portal_role', request.user.role) in {'TEACHER','ADMIN'} else 'STUDENT',
            sandbox_connection=profile, source_text=source, statements=statements)
        from .project_queue import enqueue
        transaction.on_commit(lambda: enqueue(run, {}))
    run.refresh_from_db()
    return JsonResponse(_payload(run), status=201)


@login_required
@require_http_methods(["POST"])
def project_resume(request, run_id):
    if not same_origin(request): return JsonResponse({"error": "Cross-origin request blocked. Refresh the portal page and try again."}, status=403)
    if not _can_run(request): return JsonResponse({"error": "Sign in to check a project."}, status=403)
    try: run = ProjectExecution.objects.select_related("sandbox_connection").exclude(activity_kind="grading").get(pk=run_id, owner=request.user)
    except ProjectExecution.DoesNotExist: return JsonResponse({"error": "Project run not found."}, status=404)
    if run.activity_kind != 'file':
        from .assessment_sandbox import assessment_for
        try:
            assessment_for(request,run.activity_kind,run.activity_id)
        except (ValueError,PermissionError) as exc:
            return JsonResponse({'error':str(exc)},status=403)
    if run.status == ProjectExecution.Status.COMPLETED: return JsonResponse(_payload(run))
    if run.status == ProjectExecution.Status.CANCELLED: return JsonResponse({"error": "Project run was cancelled."}, status=409)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Provide an object containing correction or variable values.'}, status=400)
    from .project_queue import enqueue
    enqueue(run, payload)
    run.refresh_from_db()
    return JsonResponse(_payload(run), status=202)


def execute_project_run(run, payload):
    correction = str(payload.get("correction") or "").strip()
    if run.activity_kind != 'file' and correction and payload.get('inputMethod') != 'typed':
        return JsonResponse({'error':'Assessment corrections must be typed in the sandbox.'},status=400)
    connection = None
    client = None
    locked = False
    try:
        client = project_session(run)
        locked = client.lock.acquire(blocking=False)
        if not locked:
            return JsonResponse({"error": "This project check is already running."}, status=409)
        run.refresh_from_db()
        if run.status == ProjectExecution.Status.CANCELLED:
            return JsonResponse({"error": "This file check was replaced by a newer check."}, status=409)
        if run.status == ProjectExecution.Status.COMPLETED:
            return JsonResponse(_payload(run))
        if client.next_index != run.next_index:
            raise ValueError('Project session progress differs from saved progress. Start a new project check.')
        from django.utils import timezone
        if run.schema_expires_at and run.schema_expires_at <= timezone.now():
            raise ValueError('This file schema has expired. Start a new file check.')
        renew_schema(run)
        failed_before = any(row.get('index') == run.next_index and row.get('status') == 'error' for row in run.results)
        if run.activity_kind == 'file' and failed_before:
            if correction_attempts_remaining(run) == 0:
                run.status = ProjectExecution.Status.PAUSED
                run.save(update_fields=('status', 'updated_at'))
                return JsonResponse(_payload(run), status=422)
        if correction:
            if run.next_index >= len(run.statements): return JsonResponse({"error": "There is no failed statement to correct."}, status=400)
            replacements = split_statements(correction, sqlplus=True, client_command=PROJECT_CLIENT_COMMAND)
            if len(replacements) != 1:
                return JsonResponse({"error": "Correct one statement at a time."}, status=400)
            run.statements[run.next_index] = replacements[0]
            run.save(update_fields=("statements", "updated_at"))
        connection = open_oracle_connection(connect_options({"user": client.schema_username,
            "password": client.secret, "connectString": run.sandbox_connection.connect_string}))
        # Recheck effective privileges on resume as well as at initial login.
        try:
            connect_info(connection, run.sandbox_connection.connect_string, client.schema_username)
        except Exception:
            close_project_session(run.pk)
            raise
        with connection.cursor() as cursor:
            cursor.execute("SELECT SYS_CONTEXT('USERENV','DB_NAME'), SYS_CONTEXT('USERENV','SERVICE_NAME'), SYS_CONTEXT('USERENV','CON_NAME'), SYS_CONTEXT('USERENV','SESSION_USER'), SYS_CONTEXT('USERENV','CURRENT_SCHEMA') FROM dual")
            target = cursor.fetchone()
        connection.close()
        connection = None
        run.results.append({"type": "connection", "profile": run.sandbox_connection.name,
            "database": target[0], "service": target[1], "container": target[2],
            "sessionUser": target[3], "schema": target[4]})
        run.save(update_fields=("results", "updated_at"))
        if not ProjectExecution.objects.filter(pk=run.pk).exclude(status='cancelled').update(status='running'):
            return JsonResponse({'error': 'This file check was replaced by a newer check.'}, status=409)
        run.status = ProjectExecution.Status.RUNNING
        if run.activity_kind == 'file' and failed_before:
            run.results.append({'type': 'correction_attempt', 'index': run.next_index})
            run.save(update_fields=('results', 'updated_at'))
        if payload.get('variables'):
            client.provide_inputs(payload['variables'])
        for index in range(run.next_index, len(run.statements)):
            if ProjectExecution.objects.filter(pk=run.pk, status=ProjectExecution.Status.CANCELLED).exists():
                return JsonResponse({"error": "This file check was replaced by a newer check."}, status=409)
            statement = run.statements[index]
            started = time.monotonic()
            try:
                output, exited = client.execute(statement)
                result = {"index": index, "status": "success", 'statement': statement,
                    "output": output, "durationMs": round((time.monotonic()-started)*1000)}
                if run.activity_kind != 'file':
                    result['inputMethod'] = 'setup' if index < run.setup_statement_count else 'typed'
                if isinstance(client.last_table, dict):
                    result['table'] = client.last_table
                run.results.append(result)
                run.next_index = index + 1
                client.next_index = run.next_index
                run.save(update_fields=("results", "next_index", "updated_at"))
                renew_schema(run)
                if exited:
                    break
            except SQLPlusInputRequired as exc:
                if ProjectExecution.objects.filter(pk=run.pk, status=ProjectExecution.Status.CANCELLED).exists():
                    return JsonResponse({'error': 'This file check was replaced by a newer check.'}, status=409)
                run.results.append({'index': index, 'status': 'input', 'inputs': exc.requests, 'statement': statement})
                run.status = ProjectExecution.Status.PAUSED
                run.save(update_fields=('results', 'status', 'updated_at'))
                return JsonResponse(_payload(run), status=422)
            except Exception as exc:
                if ProjectExecution.objects.filter(pk=run.pk, status=ProjectExecution.Status.CANCELLED).exists():
                    return JsonResponse({"error": "This file check was replaced by a newer check."}, status=409)
                pending = getattr(client, 'pending_inputs', [])
                retry_inputs = pending if isinstance(pending, list) else []
                run.results.append({"index": index, "status": "error", "error": str(exc), "statement": statement, "inputs": retry_inputs})
                run.status = ProjectExecution.Status.PAUSED
                run.save(update_fields=("results", "status", "updated_at"))
                return JsonResponse(_payload(run), status=422)
        if ProjectExecution.objects.filter(pk=run.pk, status=ProjectExecution.Status.CANCELLED).exists():
            return JsonResponse({"error": "This file check was replaced by a newer check."}, status=409)
        if run.activity_kind == 'file':
            run.status = ProjectExecution.Status.COMPLETED
            run.save(update_fields=("status", "updated_at"))
            close_project_session(run.pk)
            remove_schema(run)
        else:
            run.status = ProjectExecution.Status.READY
            run.save(update_fields=('status','updated_at'))
        return JsonResponse(_payload(run))
    except Exception as exc:
        if ProjectExecution.objects.filter(pk=run.pk, status=ProjectExecution.Status.CANCELLED).exists():
            return JsonResponse({"error": "This file check was replaced by a newer check."}, status=409)
        pending = getattr(client, 'pending_inputs', [])
        run.status = ProjectExecution.Status.PAUSED; run.results.append({"index": run.next_index, "status": "error", "error": str(exc),
            'inputs': pending if isinstance(pending, list) else []})
        run.save(update_fields=("status", "results", "updated_at"))
        return JsonResponse(_payload(run), status=422)
    finally:
        if locked:
            client.lock.release()
        if connection:
            try: connection.close()
            except Exception: pass


@login_required
@never_cache
def project_run_detail(request, run_id):
    if not same_origin(request): return JsonResponse({"error": "Cross-origin request blocked."}, status=403)
    if not _can_run(request): return JsonResponse({"error": "Sign in to check a project."}, status=403)
    scope = _report_scope(request)
    if scope is None:
        scope = ProjectExecution.objects.filter(owner=request.user).exclude(activity_kind="grading")
    try: run = scope.get(pk=run_id)
    except ProjectExecution.DoesNotExist: return JsonResponse({"error": "Project run not found."}, status=404)
    if run.owner_id == request.user.pk and run.activity_kind != 'file' and run.status in {'ready','paused'}:
        from .project_sqlplus import touch_project_session
        from django.utils import timezone
        if run.schema_expires_at and run.schema_expires_at > timezone.now() and touch_project_session(run.pk):
            renew_schema(run)
    return JsonResponse(_payload(run))
