from collections import Counter
import json
import hashlib
import hmac
import logging
import mimetypes
from pathlib import Path
from datetime import date, time, timedelta
import secrets
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ElementTree
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, password_validation, update_session_auth_hash
from django.contrib.auth.hashers import make_password
from django.contrib.auth.decorators import login_required
from django.middleware.csrf import get_token
from django.db.models import Q, Count, Sum, Max, Subquery
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template import Context, Engine
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django import forms
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from PIL import Image

from .models import (
    Announcement,
    Assignment,
    AttendanceSession,
    CompilerClientActivityEvent,
    CompilerExecutionEvent,
    CompilerActivitySlice,
    CompilerPasteEvent,
    CompilerQueryHistory,
    ClassSection,
    Course,
    AuditEvent,
    DashboardNavigationItem,
    Enrollment,
    PortalUser,
    PortalSetting,
    PendingRegistration,
    ProfileUpdateChallenge,
    RegistrationField,
    normalize_mobile_number,
    SQLTest,
    SQLTestQuestion,
    TestAnswer,
    TestAttempt,
    Submission,
    SharedCompilerCode,
    TeacherAssignment,
    TeacherActivityPreference,
)
from .otp_delivery import OTPDeliveryError, send_email_otp, send_whatsapp_otp
from .account_notifications import notify_account_event
from .attendance_daily import daily_attendance


@login_required(login_url='/portal/')
@require_http_methods(['GET', 'POST'])
def teacher_session_limits(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return JsonResponse({'error': 'Only teachers can manage session limits.'}, status=403)
    if not _same_origin(request):
        return JsonResponse({'error': 'Use the portal to manage session limits.'}, status=403)
    try:
        data = request.GET if request.method == 'GET' else json.loads(request.body or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid session limit request.'}, status=400)
    if not hasattr(data, 'get'):
        return JsonResponse({'error': 'Invalid session limit request.'}, status=400)
    try:
        class_id = int(data.get('classId') or 0)
        student_id = int(data.get('studentId') or 0)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Choose a valid class or student.'}, status=400)
    classes = ClassSection.objects.filter(teacher_assignments__teacher=request.user)
    section = classes.filter(pk=class_id).first()
    if not section:
        return JsonResponse({'error': 'Choose a class assigned to you.'}, status=403)
    if request.method == 'GET':
        return JsonResponse({'limitMinutes': section.daily_session_minutes, 'inherited': False})
    action = data.get('action')
    if action == 'save':
        try:
            minutes = int(data.get('limitMinutes'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Enter a daily allowance in minutes.'}, status=400)
        if not 1 <= minutes <= 1440:
            return JsonResponse({'error': 'Choose between 1 and 1440 minutes.'}, status=400)
        section.daily_session_minutes = minutes
        section.save(update_fields=['daily_session_minutes'])
        AuditEvent.objects.create(actor=request.user, action='session_limit', target=f'class:{section.pk}:{minutes}')
        return JsonResponse({'ok': True, 'limitMinutes': minutes})
    if action == 'reset':
        from .models import StudentDailyAccessReset
        students = PortalUser.objects.filter(role=PortalUser.Role.STUDENT, pk__in=Enrollment.objects.filter(class_section=section, active=True).values('student_id'))
        if student_id:
            students = students.filter(pk=student_id)
        elif data.get('scope') == 'student':
            return JsonResponse({'error': 'Choose a student to reset.'}, status=400)
        now = timezone.now()
        today = timezone.localdate()
        with transaction.atomic():
            # Serialize resets for this class. This prevents two simultaneous
            # bulk resets from both deciding the same daily rows are missing
            # and racing on the unique (student, day) constraint.
            ClassSection.objects.select_for_update().only('pk').get(pk=section.pk)
            student_ids = list(students.order_by().values_list('pk', flat=True))
            if student_id and not student_ids:
                return JsonResponse({'error': 'Student is not enrolled in this class.'}, status=403)
            # Keep each IN clause below Oracle's 1,000-expression limit while
            # retaining bulk writes instead of one update_or_create per student.
            for offset in range(0, len(student_ids), 500):
                batch_ids = student_ids[offset:offset + 500]
                reset_rows = StudentDailyAccessReset.objects.filter(day=today, student_id__in=batch_ids)
                existing_ids = set(reset_rows.values_list('student_id', flat=True))
                reset_rows.update(reset_at=now, reset_by_id=request.user.pk)
                missing_ids = [pk for pk in batch_ids if pk not in existing_ids]
                if missing_ids:
                    StudentDailyAccessReset.objects.bulk_create(
                        [StudentDailyAccessReset(student_id=pk, day=today, reset_at=now, reset_by_id=request.user.pk) for pk in missing_ids],
                        batch_size=500,
                    )
            count = len(student_ids)
            AuditEvent.objects.create(actor=request.user, action='session_reset', target=f'class:{section.pk}:student:{student_id}:count:{count}')
        return JsonResponse({'ok': True, 'resetCount': count})
    return JsonResponse({'error': 'Choose save or reset.'}, status=400)


@require_GET
def student_session_status(request):
    if not request.user.is_authenticated:
        return JsonResponse({'sessionExpired': True}, status=401)
    if hasattr(request, 'student_daily_allowance'):
        return JsonResponse(request.student_daily_allowance)
    from .session_policy import daily_allowance
    attendance = AttendanceSession.objects.filter(pk=request.session.get('attendance_session_id'), user=request.user).select_related('class_section').first()
    return JsonResponse(daily_allowance(request.user, attendance))


PROJECT_DIR = Path(settings.BASE_DIR) / "online-oracle-compiler"
PORTAL_ASSETS = {"workspace-ui.css", "workspace-ui.js", "workspace-session-ui.js", "workspace-daily-task.js", "setup-guide.js", "setup-guide.css", "class-management.css","class-management.js","daily-tasks.js","student-session.js", "contact-verification.js", "compiler-activity-client.js", "workspace-core.js", "editor-assist.js", "login-autofill.js", "app.js", "styles.css", "dashboard.css", "dashboard.js", "teacher-activity.js", "teacher-attendance.js", "teacher-history.js", "teacher-remote-code.js", "search-suggestions.js", "palette.css", "palette.js", "theme.css", "theme.js", "theme-controls.css", "theme-controls.js", "portal-3d.css", "oracle-sql-ide.css", "oracle-sql-ide-extra.css", "oracle-sql-ide-layout.css", "oracle-sql-ide.js", "compiler-icon-controls.css"}
TEMPLATE_ENGINE = Engine(dirs=[str(PROJECT_DIR / "templates")], autoescape=True)
PORTAL_ROLES = (PortalUser.Role.ADMIN, PortalUser.Role.TEACHER, PortalUser.Role.STUDENT)
logger = logging.getLogger(__name__)


def _dashboard_path(role):
    if role == PortalUser.Role.ADMIN:
        return "/admin/"
    return "/teacher" if role == PortalUser.Role.TEACHER else "/portal/student/"


def _available_portal_roles(user):
    return [role for role in user.portal_roles if role in PORTAL_ROLES]


def _active_portal_role(request):
    roles = _available_portal_roles(request.user)
    selected = request.session.get("portal_active_role")
    if selected in roles:
        return selected
    return roles[0] if len(roles) == 1 else None


@ensure_csrf_cookie
@require_GET
def portal_home(request):
    if request.user.is_authenticated:
        role = _active_portal_role(request)
        if role:
            return redirect(_dashboard_path(role))
        if len(_available_portal_roles(request.user)) > 1:
            return redirect("/portal/choose-role/")
        attendance_id = request.session.get("attendance_session_id")
        if attendance_id:
            now = timezone.now()
            AttendanceSession.objects.filter(pk=attendance_id, user=request.user, logout_at__isnull=True).update(
                logout_at=now, logout_estimated=False, end_reason="logout",
                compiler_active=False, is_active_now=False,
            )
            CompilerActivitySlice.objects.filter(attendance_session_id=attendance_id, ended_at__isnull=True).update(
                ended_at=now, compiler_active=False, is_active_now=False,
            )
        session_key = request.session.session_key
        if session_key:
            from .views import _close_oracle_session, _oracle_sessions
            state = _oracle_sessions.get(session_key)
            if state: _close_oracle_session(state)
        if request.user.role == PortalUser.Role.STUDENT:
            from .views import _reset_teacher_editor_session_access
            _reset_teacher_editor_session_access(request.user.pk)
        logout(request)
    response = FileResponse((PROJECT_DIR / "index.html").open("rb"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response.delete_cookie("ora_compiler_session", samesite="Strict")
    return response


@require_GET
def portal_asset(request, asset):
    if asset not in PORTAL_ASSETS:
        raise Http404
    asset_path = PROJECT_DIR / asset
    if not asset_path.is_file():
        raise Http404
    from django.utils.cache import get_conditional_response
    from django.utils.http import http_date
    stat = asset_path.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    conditional = get_conditional_response(request, etag=etag, last_modified=stat.st_mtime)
    cache_control = 'public, max-age=300' if request.GET.get('v') else 'no-cache'
    if conditional is not None:
        conditional['Cache-Control'] = cache_control
        conditional['ETag'] = etag
        conditional['Last-Modified'] = http_date(stat.st_mtime)
        return conditional
    content_type = mimetypes.guess_type(asset)[0] or "application/octet-stream"
    response = FileResponse(asset_path.open("rb"), content_type=content_type)
    response["Cache-Control"] = cache_control
    response['ETag'] = etag
    response['Last-Modified'] = http_date(stat.st_mtime)
    return response


@require_POST
def portal_login_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Sign in must be submitted from this portal."}, status=403)
    if len(request.body) > 8192:
        return JsonResponse({"error": "Sign-in request is too large."}, status=413)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Sign-in request could not be read."}, status=400)

    identifier = payload.get("username", "")
    password = payload.get("password", "")
    remember = payload.get("remember") is True
    if not isinstance(identifier, str) or not identifier.strip() or not isinstance(password, str) or not password:
        return JsonResponse({"error": "Enter your account ID and password."}, status=400)
    identifier = identifier.strip()
    account_filter = (Q(username__iexact=identifier) | Q(email__iexact=identifier)
                      | Q(teacher_number__iexact=identifier) | Q(student_number__iexact=identifier))
    mobile_number = normalize_mobile_number(identifier)
    if mobile_number:
        account_filter |= Q(mobile_number__iexact=mobile_number)
    candidates = list(PortalUser.objects.filter(account_filter).distinct()[:2])
    if len(candidates) != 1:
        message = "This sign-in detail matches more than one account. Use your assigned account ID." if len(candidates) > 1 else "The account ID, email, mobile number, or password could not be verified."
        return JsonResponse({"error": message}, status=401)

    user = authenticate(request, username=candidates[0].get_username(), password=password)
    if not user or not user.is_active:
        return JsonResponse({"error": "The account ID, email, mobile number, or password could not be verified."}, status=401)
    if user.registration_status != PortalUser.RegistrationStatus.APPROVED:
        return JsonResponse({"error": "This account is awaiting portal approval. Contact your administrator."}, status=403)
    roles = _available_portal_roles(user)
    if not roles:
        return JsonResponse({"error": "This account does not have a portal role. Contact your administrator."}, status=403)

    old_session_key = request.session.session_key
    if PortalUser.Role.STUDENT in roles and user.last_login is None:
        user.class_selection_required = True
        user.save(update_fields=("class_selection_required",))
    if user.role == PortalUser.Role.STUDENT:
        from .session_policy import daily_allowance
        allowance = daily_allowance(user)
        if allowance['restricted'] and allowance['remainingSeconds'] <= 0:
            return JsonResponse({'error': 'Your daily portal allowance is used. Ask your teacher to reset it, or return tomorrow.'}, status=403)
    login(request, user)
    request.session["portal_login_at"] = timezone.now().timestamp()
    request.session.pop("portal_active_role", None)
    request.session.set_expiry(30 * 24 * 60 * 60 if remember else 0)
    if old_session_key and old_session_key != request.session.session_key:
        from .views import _close_oracle_session, _lock, _oracle_sessions
        with _lock:
            oracle_state = _oracle_sessions.pop(old_session_key, None)
            if oracle_state and oracle_state.get("user_id") not in (None, user.pk):
                _close_oracle_session(oracle_state)
            elif oracle_state:
                _oracle_sessions[request.session.session_key] = oracle_state
    if user.role == PortalUser.Role.STUDENT:
        from .models import CompilerActivitySlice
        with transaction.atomic():
            locked_user = PortalUser.objects.select_for_update().get(pk=user.pk)
            previous_key = locked_user.active_student_session_key
            token = secrets.token_hex(32)
            locked_user.active_student_session_token = token
            locked_user.active_student_session_key = request.session.session_key
            locked_user.save(update_fields=['active_student_session_token', 'active_student_session_key'])
            from .views import _reset_teacher_editor_session_access
            _reset_teacher_editor_session_access(user.pk)
            now = timezone.now()
            previous_ids = list(AttendanceSession.objects.filter(user=user, logout_at__isnull=True).values_list('pk', flat=True))
            AttendanceSession.objects.filter(pk__in=previous_ids).update(logout_at=now, logout_estimated=False, end_reason='replaced_login', compiler_active=False, is_active_now=False)
            CompilerActivitySlice.objects.filter(attendance_session_id__in=previous_ids, ended_at__isnull=True).update(ended_at=now, compiler_active=False, is_active_now=False)
            attendance = AttendanceSession.objects.create(user=user, source='portal')
            request.session['student_session_token'] = token
        if previous_key and previous_key != request.session.session_key:
            from .views import _close_oracle_session, _lock, _oracle_sessions
            with _lock:
                previous_state = _oracle_sessions.pop(previous_key, None)
            if previous_state: _close_oracle_session(previous_state)
    else:
        attendance = AttendanceSession.objects.create(user=user, source="portal")
    request.session["attendance_session_id"] = attendance.pk
    AuditEvent.objects.create(actor=user, action="login", target=user.username)
    next_page = "/portal/choose-role/" if len(roles) > 1 else _dashboard_path(roles[0])
    if roles == [PortalUser.Role.STUDENT] and user.class_selection_required:
        next_page = "/portal/choose-class/"
    return JsonResponse({"ok": True, "next": next_page})


@login_required(login_url="/portal/")
@require_GET
def portal_choose_role(request):
    roles = _available_portal_roles(request.user)
    if len(roles) < 2:
        return redirect(_dashboard_path(roles[0]) if roles else "/portal/")
    labels = dict(PortalUser.Role.choices)
    context = {
        "user": request.user,
        "role_options": [{"value": role, "label": labels[role]} for role in roles],
        "username": request.user.username,
    }
    return _render_dashboard("role-choice.html", context, request)


@login_required(login_url="/portal/")
@require_POST
def portal_select_role(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Role selection must be submitted from this portal."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Role selection could not be read."}, status=400)
    role = payload.get("role") if isinstance(payload, dict) else None
    if role not in _available_portal_roles(request.user):
        return JsonResponse({"error": "Choose a role assigned to this account."}, status=403)
    request.session["portal_active_role"] = role
    if role == PortalUser.Role.STUDENT and request.user.class_selection_required:
        return JsonResponse({"ok": True, "next": "/portal/choose-class/"})
    return JsonResponse({"ok": True, "next": _dashboard_path(role)})


def _otp_digest(challenge_id, code):
    message = f"{challenge_id}:{code}".encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _create_registered_portal_user(registration_data, profile_data, password_hash, email_verified=False, mobile_verified=False):
    setting = PortalSetting.objects.filter(pk=1).first()
    role = PortalUser.Role.TEACHER if registration_data.get("role") == "teacher" else PortalUser.Role.STUDENT
    auto_approved = role == PortalUser.Role.STUDENT and bool(setting and setting.auto_approve_students)
    id_field = "teacher_number" if role == PortalUser.Role.TEACHER else "student_number"
    for attempt in range(10):
        candidate_id = ""
        try:
            with transaction.atomic():
                candidate_id = PortalUser.next_account_id(role)
                user = PortalUser(
                    username=candidate_id,
                    first_name=registration_data["first_name"],
                    middle_name=registration_data.get("middle_name", ""),
                    last_name=registration_data.get("last_name", ""),
                    email=registration_data.get("email", ""),
                    mobile_number=registration_data.get("mobile_number", ""),
                    profile_data=profile_data,
                    password=password_hash,
                    role=role,
                    class_selection_required=role == PortalUser.Role.STUDENT,
                    registration_status=(PortalUser.RegistrationStatus.APPROVED if auto_approved else PortalUser.RegistrationStatus.PENDING_ADMIN),
                    is_active=auto_approved,
                    email_verified=email_verified,
                    mobile_verified=mobile_verified,
                    approved_at=timezone.now() if auto_approved else None,
                )
                setattr(user, id_field, candidate_id)
                user.save()
                AuditEvent.objects.create(
                    actor=user,
                    action=f"portal_{role.lower()}_registration_auto_approved" if auto_approved else f"portal_{role.lower()}_registration_submitted",
                    target=candidate_id,
                )
            return user
        except IntegrityError:
            if candidate_id and PortalUser.objects.filter(username__iexact=candidate_id).exists():
                if attempt < 9:
                    continue
                raise RuntimeError("Could not allocate a unique account ID.")
            logger.exception("Registration database integrity failure while creating account ID %s", candidate_id or "<unallocated>")
            raise RuntimeError("Registration could not be saved because of a database issue.") from None
    raise RuntimeError("Could not allocate a unique account ID.")


def _registration_success_message(user):
    if user.registration_status == PortalUser.RegistrationStatus.APPROVED:
        return f"Registration complete. Your account ID is {user.username}. You can sign in now."
    return f"Registration submitted for review. Your account ID is {user.username}. You can sign in after an administrator approves your account."


@require_POST
def portal_register_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Registration must be submitted from this portal."}, status=403)
    if len(request.body) > 8192:
        return JsonResponse({"error": "Registration details are too large."}, status=413)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Registration details could not be read."}, status=400)

    first_name = str(payload.get("firstName", "")).strip()
    role = str(payload.get("role", "student")).strip().lower()
    if role not in ("student", "teacher"):
        return JsonResponse({"error": "Choose student or teacher registration."}, status=400)
    middle_name = str(payload.get("middleName", "")).strip()
    last_name = str(payload.get("lastName", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    raw_mobile_number = str(payload.get("mobileNumber", "")).strip()
    mobile_number = normalize_mobile_number(raw_mobile_number)
    password = payload.get("password", "")
    required_fields = _registration_required_fields(role)
    field_values = {"first_name": first_name, "middle_name": middle_name, "last_name": last_name, "email": email, "mobile_number": mobile_number}
    missing = [field for field in required_fields if not field_values.get(field)]
    custom_values = payload.get("customFields", {})
    if not isinstance(custom_values, dict):
        return JsonResponse({"error": "Custom registration fields could not be read."}, status=400)
    custom_fields = list(RegistrationField.objects.filter(setting_id=1, is_active=True)) if role == "student" else []
    profile_data = {}
    for field in custom_fields:
        value = str(custom_values.get(field.key, "")).strip()
        if field.required and not value:
            missing.append(field.label)
        if len(value) > 2000:
            return JsonResponse({"error": f"{field.label} must be 2,000 characters or fewer."}, status=400)
        if value and field.input_type == RegistrationField.InputType.EMAIL:
            try:
                validate_email(value)
            except ValidationError:
                return JsonResponse({"error": f"Enter a valid {field.label.lower()}."}, status=400)
        if value and field.input_type == RegistrationField.InputType.PHONE:
            normalized = normalize_mobile_number(value)
            if not normalized:
                return JsonResponse({"error": f"Enter a valid {field.label.lower()} with 7 to 15 digits."}, status=400)
            value = normalized
        profile_data[field.key] = value
    if len(middle_name) > 150:
        return JsonResponse({"error": "Middle name must be 150 characters or fewer."}, status=400)
    if missing or not isinstance(password, str) or not password:
        return JsonResponse({"error": "Complete all required fields and enter a password."}, status=400)
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"error": "Enter a valid email address."}, status=400)
    if raw_mobile_number and not mobile_number:
        return JsonResponse({"error": "Enter a valid mobile number with 7 to 15 digits."}, status=400)
    if email and PortalUser.objects.filter(email__iexact=email).exists():
        return JsonResponse({"error": "An account already uses this email. Ask the administrator to add another role to that account instead of creating a duplicate."}, status=409)
    if mobile_number and PortalUser.objects.filter(mobile_number=mobile_number).exists():
        return JsonResponse({"error": "This mobile number is already linked to an account. Use that account to sign in."}, status=409)

    user = PortalUser(
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        email=email,
        mobile_number=mobile_number,
        profile_data=profile_data,
        role=PortalUser.Role.STUDENT,
    )
    try:
        password_validation.validate_password(password, user)
    except ValidationError as error:
        return JsonResponse({"error": " ".join(error.messages)}, status=400)

    setting = PortalSetting.objects.filter(pk=1).first()
    verify_email = bool(setting and setting.email_verification_required)
    verify_mobile = bool(setting and setting.mobile_verification_required)
    registration_data = {
        "role": role,
        "first_name": first_name,
        "middle_name": middle_name,
        "last_name": last_name,
        "email": email,
        "mobile_number": mobile_number,
    }
    password_hash = make_password(password)
    if verify_email or verify_mobile:
        if verify_email and (not setting.gmail_login_email or not setting.gmail_sender_email or not setting.has_gmail_app_password()):
            return JsonResponse({"error": "Email verification is enabled, but Gmail OTP delivery is not configured in Admin."}, status=503)
        PendingRegistration.objects.filter(expires_at__lte=timezone.now()).delete()
        challenge = PendingRegistration(
            registration_data=registration_data,
            profile_data=profile_data,
            password_hash=password_hash,
        )
        email_code = f"{secrets.randbelow(1_000_000):06d}" if verify_email else ""
        mobile_code = f"{secrets.randbelow(1_000_000):06d}" if verify_mobile else ""
        challenge.email_code_hash = _otp_digest(challenge.pk, email_code) if email_code else ""
        challenge.mobile_code_hash = _otp_digest(challenge.pk, mobile_code) if mobile_code else ""
        challenge.save()
        try:
            if verify_email:
                send_email_otp(setting, email, email_code)
            if verify_mobile:
                send_whatsapp_otp(setting, mobile_number, mobile_code)
        except OTPDeliveryError as error:
            challenge.delete()
            return JsonResponse({"error": str(error)}, status=502)
        channels = (["email"] if verify_email else []) + (["mobile"] if verify_mobile else [])
        return JsonResponse({
            "ok": True,
            "requiresVerification": True,
            "challengeId": str(challenge.pk),
            "channels": channels,
            "message": "Enter the verification code sent to your " + ("email and WhatsApp number." if len(channels) == 2 else ("email address." if verify_email else "WhatsApp number.")),
        }, status=202)

    try:
        created_user = _create_registered_portal_user(registration_data, profile_data, password_hash)
    except RuntimeError as error:
        return JsonResponse({"error": str(error)}, status=500)
    notification_failures = notify_account_event(created_user, "registered", request=request)
    message = _registration_success_message(created_user)
    if notification_failures:
        message += " A confirmation could not be delivered through every contact channel. Contact the portal administrator."
    return JsonResponse({
        "ok": True,
        "accountId": created_user.username,
        "message": message,
    }, status=201)


@require_POST
def portal_verify_registration_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Verification must be submitted from this portal."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Verification details could not be read."}, status=400)
    challenge_id = payload.get("challengeId") if isinstance(payload, dict) else None
    if not isinstance(challenge_id, str):
        return JsonResponse({"error": "Start registration again to request new verification codes."}, status=400)
    email_code = str(payload.get("emailOtp", "")).strip()
    mobile_code = str(payload.get("mobileOtp", "")).strip()
    with transaction.atomic():
        try:
            challenge = PendingRegistration.objects.select_for_update().get(pk=challenge_id)
        except PendingRegistration.DoesNotExist:
            return JsonResponse({"error": "This verification request is no longer available. Start registration again."}, status=404)
        if challenge.expires_at <= timezone.now() or challenge.attempts >= 5:
            challenge.delete()
            return JsonResponse({"error": "The verification code expired or too many attempts were made. Start registration again."}, status=410)
        invalid = False
        if challenge.email_code_hash:
            invalid |= not (len(email_code) == 6 and hmac.compare_digest(challenge.email_code_hash, _otp_digest(challenge.pk, email_code)))
        if challenge.mobile_code_hash:
            invalid |= not (len(mobile_code) == 6 and hmac.compare_digest(challenge.mobile_code_hash, _otp_digest(challenge.pk, mobile_code)))
        if invalid:
            challenge.attempts += 1
            challenge.save(update_fields=("attempts",))
            return JsonResponse({"error": "The verification code is incorrect. Check the code and try again."}, status=400)
        registration_data = challenge.registration_data
        email = registration_data.get("email", "")
        mobile_number = registration_data.get("mobile_number", "")
        if email and PortalUser.objects.filter(email__iexact=email).exists():
            challenge.delete()
            return JsonResponse({"error": "An account now uses this email. Ask the administrator to add another role to that account."}, status=409)
        if mobile_number and PortalUser.objects.filter(mobile_number=mobile_number).exists():
            challenge.delete()
            return JsonResponse({"error": "This mobile number is now linked to another account."}, status=409)
        try:
            created_user = _create_registered_portal_user(
                challenge.registration_data,
                challenge.profile_data,
                challenge.password_hash,
                email_verified=bool(challenge.email_code_hash),
                mobile_verified=bool(challenge.mobile_code_hash),
            )
        except RuntimeError as error:
            return JsonResponse({"error": str(error)}, status=500)
        challenge.delete()
    notification_failures = notify_account_event(created_user, "registered", request=request)
    message = _registration_success_message(created_user)
    if notification_failures:
        message += " A confirmation could not be delivered through every contact channel. Contact the portal administrator."
    return JsonResponse({
        "ok": True,
        "accountId": created_user.username,
        "message": message,
    }, status=201)


@require_POST
def portal_resend_registration_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Verification must be requested from this portal."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Verification details could not be read."}, status=400)
    challenge_id = payload.get("challengeId") if isinstance(payload, dict) else None
    if not isinstance(challenge_id, str):
        return JsonResponse({"error": "Start registration again to request a new code."}, status=400)
    with transaction.atomic():
        try:
            challenge = PendingRegistration.objects.select_for_update().get(pk=challenge_id)
        except PendingRegistration.DoesNotExist:
            challenge = None
        if not challenge or challenge.attempts >= 5:
            return JsonResponse({"error": "This verification request is no longer available. Return to registration and submit your details again."}, status=404)
        # The expiry is refreshed on each send, so it also records when the latest code was sent.
        last_sent_at = challenge.expires_at - timedelta(minutes=10)
        seconds_since_send = (timezone.now() - last_sent_at).total_seconds()
        if seconds_since_send < 30:
            wait_seconds = max(1, 30 - int(seconds_since_send))
            return JsonResponse({"error": f"Please wait {wait_seconds} seconds before requesting another code."}, status=429)
        setting = PortalSetting.objects.filter(pk=1).first()
        email = challenge.registration_data.get("email", "")
        mobile_number = challenge.registration_data.get("mobile_number", "")
        email_code = f"{secrets.randbelow(1_000_000):06d}" if challenge.email_code_hash else ""
        mobile_code = f"{secrets.randbelow(1_000_000):06d}" if challenge.mobile_code_hash else ""
        if email_code and (not setting or not setting.gmail_login_email or not setting.gmail_sender_email or not setting.has_gmail_app_password()):
            return JsonResponse({"error": "Email verification is not currently configured. Contact the portal administrator."}, status=503)
        challenge.email_code_hash = _otp_digest(challenge.pk, email_code) if email_code else ""
        challenge.mobile_code_hash = _otp_digest(challenge.pk, mobile_code) if mobile_code else ""
        challenge.attempts = 0
        challenge.expires_at = timezone.now() + timedelta(minutes=10)
        challenge.save(update_fields=("email_code_hash", "mobile_code_hash", "attempts", "expires_at"))
        try:
            if email_code:
                send_email_otp(setting, email, email_code)
            if mobile_code:
                send_whatsapp_otp(setting, mobile_number, mobile_code)
        except OTPDeliveryError as error:
            logger.warning("Registration OTP resend failed: %s", error)
            return JsonResponse({"error": "The code could not be delivered. Please try again shortly."}, status=502)
    return JsonResponse({"ok": True, "message": "A new verification code has been sent. If the email hasn’t arrived, check your Spam or Junk folder."})


@require_POST
def portal_cancel_registration_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Registration must be changed from this portal."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Registration details could not be read."}, status=400)
    challenge_id = payload.get("challengeId") if isinstance(payload, dict) else None
    if isinstance(challenge_id, str):
        PendingRegistration.objects.filter(pk=challenge_id).delete()
    return JsonResponse({"ok": True})


@require_GET
def portal_registration_config(request):
    role = request.GET.get("role", "student").lower()
    if role not in ("student", "teacher"):
        role = "student"
    custom_fields = RegistrationField.objects.filter(setting_id=1, is_active=True).values(
        "key", "label", "input_type", "required"
    ) if role == "student" else []
    return JsonResponse({"requiredFields": _registration_required_fields(role), "customFields": list(custom_fields)})


def _registration_required_fields(role="student"):
    allowed = {"first_name", "middle_name", "last_name", "email", "mobile_number"}
    setting = PortalSetting.objects.filter(pk=1).first()
    configured = (setting.registration_required_fields if setting else ["first_name", "email", "mobile_number"]) if role == "student" else ["first_name", "email", "mobile_number"]
    if not isinstance(configured, list):
        return ["first_name", "email", "mobile_number"]
    required = [field for field in configured if field in allowed]
    if setting and setting.email_verification_required and "email" not in required:
        required.append("email")
    if setting and setting.mobile_verification_required and "mobile_number" not in required:
        required.append("mobile_number")
    return required


def _same_origin(request):
    from .views import same_origin
    return same_origin(request)


@require_GET
def portal_preferences(request):
    setting = PortalSetting.objects.filter(pk=1).first()
    return JsonResponse({"defaultAccent": setting.default_accent if setting else "violet"})


@require_POST
@login_required(login_url="/portal/")
def set_portal_default_accent(request):
    if not _same_origin(request):
        return JsonResponse({"error": "This setting must be changed from the portal."}, status=403)
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return JsonResponse({"error": "Only teachers can change the website default color."}, status=403)
    if len(request.body) > 2048:
        return JsonResponse({"error": "The request is too large."}, status=413)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "The color setting could not be read."}, status=400)
    accent = payload.get("accent")
    allowed = {"violet", "blue", "teal", "indigo", "cyan", "emerald", "amber", "rose", "orange", "slate"}
    if accent not in allowed:
        return JsonResponse({"error": "Choose one of the available accent colors."}, status=400)
    PortalSetting.objects.update_or_create(pk=1, defaults={"default_accent": accent})
    AuditEvent.objects.create(actor=request.user, action="portal_default_accent_changed", target=accent)
    return JsonResponse({"ok": True, "defaultAccent": accent})


@require_GET
@login_required(login_url="/portal/")
def portal_profile_api(request):
    user = request.user
    return JsonResponse({
        "username": user.username,
        "role": (_active_portal_role(request) or user.role).title(),
        "firstName": user.first_name,
        "middleName": user.middle_name,
        "lastName": user.last_name,
        "email": user.email,
        "mobileNumber": user.mobile_number,
        "avatarUrl": _portal_avatar_url(user),
    })


def _portal_avatar_url(user):
    return f"/api/portal/avatar?v={user.avatar_image.name}" if user.avatar_image else ""


@require_GET
@login_required(login_url="/portal/")
def portal_avatar_api(request):
    avatar = request.user.avatar_image
    if not avatar:
        raise Http404("No profile image is set.")
    try:
        avatar_file = avatar.open("rb")
    except (OSError, ValueError):
        raise Http404("Profile image is unavailable.")
    content_type = mimetypes.guess_type(avatar.name)[0] or "application/octet-stream"
    response = FileResponse(avatar_file, content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_POST
@login_required(login_url="/portal/")
def portal_avatar_update_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Profile photo updates must be submitted from the portal."}, status=403)
    if _active_portal_role(request) == PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Profile photo updates are disabled for student accounts."}, status=403)
    avatar_upload = request.FILES.get("avatar")
    if not avatar_upload:
        return JsonResponse({"error": "Choose a profile photo to upload."}, status=400)
    if avatar_upload.size > 2 * 1024 * 1024:
        return JsonResponse({"error": "Choose a profile image no larger than 2 MB."}, status=413)
    try:
        avatar_upload = forms.ImageField().clean(avatar_upload)
    except (ValidationError, Image.DecompressionBombError, OSError, ValueError):
        return JsonResponse({"error": "Choose a valid JPG, PNG, or WebP image."}, status=400)
    image = avatar_upload.image
    if image.format not in {"JPEG", "PNG", "WEBP"}:
        return JsonResponse({"error": "Choose a JPG, PNG, or WebP image."}, status=400)
    width, height = image.size
    if width > 4096 or height > 4096 or width * height > 12_000_000:
        return JsonResponse({"error": "The image dimensions must be 4096 pixels or smaller."}, status=400)
    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image.format]
    avatar_upload.name = f"avatar.{extension}"
    avatar_upload.seek(0)

    user = request.user
    old_avatar_name = user.avatar_image.name
    user.avatar_image = avatar_upload
    user.save(update_fields=["avatar_image"])
    if old_avatar_name and old_avatar_name != user.avatar_image.name:
        user.avatar_image.storage.delete(old_avatar_name)
    AuditEvent.objects.create(actor=user, action="profile_photo_updated", target=user.username)
    return JsonResponse({"ok": True, "avatarUrl": _portal_avatar_url(user), "message": "Profile photo updated."})


@require_POST
@login_required(login_url="/portal/")
def portal_profile_update_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Profile updates must be submitted from the portal."}, status=403)
    avatar_upload = None
    if (request.content_type or "").startswith("multipart/form-data"):
        payload = request.POST
        avatar_upload = request.FILES.get("avatar")
        if avatar_upload and _active_portal_role(request) == PortalUser.Role.STUDENT:
            return JsonResponse({"error": "Profile photo updates are disabled for student accounts."}, status=403)
        if avatar_upload and avatar_upload.size > 2 * 1024 * 1024:
            return JsonResponse({"error": "Choose a profile image no larger than 2 MB."}, status=413)
    else:
        if len(request.body) > 4096:
            return JsonResponse({"error": "The profile update is too large."}, status=413)
        try:
            payload = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "The profile update could not be read."}, status=400)

    if avatar_upload:
        try:
            avatar_upload = forms.ImageField().clean(avatar_upload)
        except (ValidationError, Image.DecompressionBombError, OSError, ValueError):
            return JsonResponse({"error": "Choose a valid JPG, PNG, or WebP image."}, status=400)
        image = avatar_upload.image
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            return JsonResponse({"error": "Choose a JPG, PNG, or WebP image."}, status=400)
        width, height = image.size
        if width > 4096 or height > 4096 or width * height > 12_000_000:
            return JsonResponse({"error": "The image dimensions must be 4096 pixels or smaller."}, status=400)
        extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image.format]
        avatar_upload.name = f"avatar.{extension}"
        avatar_upload.seek(0)

    values = {
        "first_name": str(payload.get("firstName", "")).strip(),
        "middle_name": str(payload.get("middleName", "")).strip(),
        "last_name": str(payload.get("lastName", "")).strip(),
        "email": str(payload.get("email", "")).strip().lower(),
        "mobile_number": str(payload.get("mobileNumber", "")).strip(),
    }
    if any(len(values[name]) > 150 for name in ("first_name", "middle_name", "last_name")):
        return JsonResponse({"error": "Names must be 150 characters or fewer."}, status=400)
    if len(values["email"]) > 254:
        return JsonResponse({"error": "The email address must be 254 characters or fewer."}, status=400)
    if values["email"]:
        try:
            validate_email(values["email"])
        except ValidationError:
            return JsonResponse({"error": "Enter a valid email address."}, status=400)
        if PortalUser.objects.filter(email__iexact=values["email"]).exclude(pk=request.user.pk).exists():
            return JsonResponse({"error": "Another account already uses this email address."}, status=409)
    mobile_number = normalize_mobile_number(values["mobile_number"])
    if values["mobile_number"] and not mobile_number:
        return JsonResponse({"error": "Enter a valid mobile number with 7 to 15 digits."}, status=400)
    if mobile_number and PortalUser.objects.filter(mobile_number=mobile_number).exclude(pk=request.user.pk).exists():
        return JsonResponse({"error": "Another account already uses this mobile number."}, status=409)

    setting = PortalSetting.objects.filter(pk=1).first()
    email_changed = values["email"] != request.user.email
    mobile_changed = mobile_number != request.user.mobile_number
    verify_email = email_changed and bool(setting and setting.email_verification_required)
    verify_mobile = mobile_changed and bool(setting and setting.mobile_verification_required)
    if verify_email and not values["email"]:
        return JsonResponse({"error": "Enter an email address so it can be verified."}, status=400)
    if verify_mobile and not mobile_number:
        return JsonResponse({"error": "Enter a mobile number so it can be verified."}, status=400)
    if verify_email and (not setting.gmail_login_email or not setting.gmail_sender_email or not setting.has_gmail_app_password()):
        return JsonResponse({"error": "Email verification is enabled, but Gmail OTP delivery is not configured in Admin."}, status=503)

    user = request.user
    challenge = None
    if verify_email or verify_mobile:
        ProfileUpdateChallenge.objects.filter(user=user).delete()
        ProfileUpdateChallenge.objects.filter(expires_at__lte=timezone.now()).delete()
        email_code = f"{secrets.randbelow(1_000_000):06d}" if verify_email else ""
        mobile_code = f"{secrets.randbelow(1_000_000):06d}" if verify_mobile else ""
        challenge = ProfileUpdateChallenge(
            user=user,
            email=values["email"] if verify_email else "",
            mobile_number=mobile_number if verify_mobile else "",
        )
        challenge.email_code_hash = _otp_digest(challenge.pk, email_code) if email_code else ""
        challenge.mobile_code_hash = _otp_digest(challenge.pk, mobile_code) if mobile_code else ""
        challenge.save()
        try:
            if verify_email:
                send_email_otp(setting, values["email"], email_code)
            if verify_mobile:
                send_whatsapp_otp(setting, mobile_number, mobile_code)
        except OTPDeliveryError as error:
            challenge.delete()
            return JsonResponse({"error": str(error)}, status=502)

    user.first_name = values["first_name"]
    user.middle_name = values["middle_name"]
    user.last_name = values["last_name"]
    if email_changed and not verify_email:
        user.email = values["email"]
        user.email_verified = False
    if mobile_changed and not verify_mobile:
        user.mobile_number = mobile_number
        user.mobile_verified = False
    update_fields = ["first_name", "middle_name", "last_name", "email", "mobile_number", "email_verified", "mobile_verified"]
    old_avatar_name = user.avatar_image.name
    if avatar_upload:
        user.avatar_image = avatar_upload
        update_fields.append("avatar_image")
    user.save(update_fields=update_fields)
    if avatar_upload and old_avatar_name and old_avatar_name != user.avatar_image.name:
        user.avatar_image.storage.delete(old_avatar_name)
    AuditEvent.objects.create(actor=user, action="profile_updated", target=user.username)
    response_data = {
        "ok": True,
        "username": user.username,
        "role": (_active_portal_role(request) or user.role).title(),
        "firstName": user.first_name,
        "middleName": user.middle_name,
        "lastName": user.last_name,
        "email": user.email,
        "mobileNumber": user.mobile_number,
        "avatarUrl": _portal_avatar_url(user),
    }
    if challenge:
        channels = (["email"] if verify_email else []) + (["mobile"] if verify_mobile else [])
        response_data.update({
            "requiresVerification": True,
            "challengeId": str(challenge.pk),
            "channels": channels,
            "message": "Your other profile changes are saved. Enter the code sent to your " + ("new email address and WhatsApp number." if len(channels) == 2 else ("new email address." if verify_email else "new WhatsApp number.")),
        })
        return JsonResponse(response_data, status=202)
    return JsonResponse(response_data)


@require_POST
@login_required(login_url="/portal/")
def portal_profile_verify_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Verification must be submitted from this portal."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Verification details could not be read."}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("challengeId"), str):
        return JsonResponse({"error": "Start the profile update again to request verification codes."}, status=400)

    email_code = str(payload.get("emailOtp", "")).strip()
    mobile_code = str(payload.get("mobileOtp", "")).strip()
    with transaction.atomic():
        try:
            challenge = ProfileUpdateChallenge.objects.select_for_update().get(
                pk=payload["challengeId"], user=request.user,
            )
        except ProfileUpdateChallenge.DoesNotExist:
            return JsonResponse({"error": "This verification request is no longer available. Update your profile again."}, status=404)
        if challenge.expires_at <= timezone.now() or challenge.attempts >= 5:
            challenge.delete()
            return JsonResponse({"error": "The verification code expired or too many attempts were made. Update your profile again."}, status=410)
        invalid = False
        if challenge.email_code_hash:
            invalid |= not (len(email_code) == 6 and hmac.compare_digest(challenge.email_code_hash, _otp_digest(challenge.pk, email_code)))
        if challenge.mobile_code_hash:
            invalid |= not (len(mobile_code) == 6 and hmac.compare_digest(challenge.mobile_code_hash, _otp_digest(challenge.pk, mobile_code)))
        if invalid:
            challenge.attempts += 1
            challenge.save(update_fields=("attempts",))
            return JsonResponse({"error": "The verification code is incorrect. Check it and try again."}, status=400)

        user = request.user
        if challenge.email and PortalUser.objects.filter(email__iexact=challenge.email).exclude(pk=user.pk).exists():
            challenge.delete()
            return JsonResponse({"error": "Another account now uses that email address. Start the profile update again."}, status=409)
        if challenge.mobile_number and PortalUser.objects.filter(mobile_number=challenge.mobile_number).exclude(pk=user.pk).exists():
            challenge.delete()
            return JsonResponse({"error": "Another account now uses that mobile number. Start the profile update again."}, status=409)

        update_fields = []
        if challenge.email:
            user.email = challenge.email
            user.email_verified = True
            update_fields.extend(("email", "email_verified"))
        if challenge.mobile_number:
            user.mobile_number = challenge.mobile_number
            user.mobile_verified = True
            update_fields.extend(("mobile_number", "mobile_verified"))
        user.save(update_fields=update_fields)
        challenge.delete()
        AuditEvent.objects.create(actor=user, action="profile_contacts_verified", target=user.username)

    return JsonResponse({
        "ok": True,
        "username": user.username,
        "role": (_active_portal_role(request) or user.role).title(),
        "firstName": user.first_name,
        "middleName": user.middle_name,
        "lastName": user.last_name,
        "email": user.email,
        "mobileNumber": user.mobile_number,
        "avatarUrl": _portal_avatar_url(user),
    })


@require_POST
@login_required(login_url="/portal/")
def portal_profile_verification_cancel_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Verification cancellation must be submitted from this portal."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Verification details could not be read."}, status=400)
    challenge_id = payload.get("challengeId") if isinstance(payload, dict) else None
    if isinstance(challenge_id, str):
        ProfileUpdateChallenge.objects.filter(pk=challenge_id, user=request.user).delete()
    return JsonResponse({"ok": True})


@require_POST
@login_required(login_url="/portal/")
def portal_password_update_api(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Password updates must be submitted from the portal."}, status=403)
    if len(request.body) > 4096:
        return JsonResponse({"error": "The password update is too large."}, status=413)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "The password update could not be read."}, status=400)
    current_password = str(payload.get("currentPassword", ""))
    new_password = str(payload.get("newPassword", ""))
    if not request.user.check_password(current_password):
        return JsonResponse({"error": "The current password is incorrect."}, status=400)
    if new_password != str(payload.get("confirmPassword", "")):
        return JsonResponse({"error": "The new passwords do not match."}, status=400)
    try:
        password_validation.validate_password(new_password, request.user)
    except ValidationError as error:
        return JsonResponse({"error": " ".join(error.messages)}, status=400)
    request.user.set_password(new_password)
    request.user.save(update_fields=("password",))
    update_session_auth_hash(request, request.user)
    AuditEvent.objects.create(actor=request.user, action="password_changed", target=request.user.username)
    return JsonResponse({"ok": True, "message": "Your password has been updated."})


@require_POST
@login_required(login_url="/portal/")
def portal_logout_api(request):
    session_key = request.session.session_key
    from .views import _close_oracle_session, _lock, _oracle_sessions, _reset_teacher_editor_session_access
    oracle_state = _oracle_sessions.get(session_key) if session_key else None
    if oracle_state:
        _close_oracle_session(oracle_state)
    if request.user.role == PortalUser.Role.STUDENT:
        _reset_teacher_editor_session_access(request.user.pk)
    attendance_id = request.session.get("attendance_session_id")
    if attendance_id:
        attendance = AttendanceSession.objects.filter(pk=attendance_id, user=request.user, logout_at__isnull=True).first()
        if attendance:
            logout_time = timezone.now()
            elapsed = max(0, min(int((logout_time - attendance.last_heartbeat_at).total_seconds()), 45))
            if elapsed:
                if attendance.is_active_now: attendance.active_seconds += elapsed
                else: attendance.idle_seconds += elapsed
            attendance.logout_at = logout_time
            attendance.logout_estimated = False
            attendance.end_reason = "logout"
            attendance.compiler_active = False
            attendance.is_active_now = False
            attendance.save(update_fields=("active_seconds", "idle_seconds", "logout_at", "logout_estimated", "end_reason", "compiler_active", "is_active_now"))
            CompilerActivitySlice.objects.filter(attendance_session=attendance, ended_at__isnull=True).update(
                ended_at=logout_time, compiler_active=False, is_active_now=False,
            )
    AuditEvent.objects.create(actor=request.user, action="logout", target=request.user.username)
    logout(request)
    response = JsonResponse({"ok": True, "next": "/portal/"})
    response.delete_cookie("ora_compiler_session", samesite="Strict")
    return response


def _render_dashboard(template_name, context, request=None):
    template = TEMPLATE_ENGINE.get_template(template_name)
    if request is not None:
        context = {**context, "csrf_token": get_token(request)}
    response = HttpResponse(template.render(Context(context)), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


@login_required(login_url="/portal/")
@require_GET
def portal_profile_page(request):
    context = _profile_dashboard_context(request)
    return _render_dashboard("profile-dashboard.html", context, request)


@login_required(login_url="/portal/")
@require_GET
def portal_profile_edit_page(request):
    return redirect("/portal/profile/")


def _profile_dashboard_context(request):
    role = _active_portal_role(request) or request.user.role
    role_labels = dict(PortalUser.Role.choices)
    return {
        "user": request.user,
        "portal_role": role,
        "profile_role": role_labels.get(role, role.title()),
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(role),
        "is_profile_page": True,
        "dashboard_home_path": _dashboard_path(role),
    }


@login_required(login_url="/portal/")
def teacher_dashboard(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    assignments = TeacherAssignment.objects.filter(teacher=request.user).select_related("class_section", "class_section__course", "lab", "class_subject__subject").order_by("class_section__course__name")
    class_ids = assignments.values_list("class_section_id", flat=True)
    submissions = Submission.objects.filter(assignment__created_by=request.user).select_related("assignment", "student").order_by("-submitted_at")[:6]
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "dashboard_home_path": _dashboard_path(PortalUser.Role.TEACHER),
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "classes": assignments,
        "class_count": ClassSection.objects.filter(pk__in=assignments.order_by().values("class_section_id")).count(),
        "student_count": Enrollment.objects.filter(class_section_id__in=class_ids, active=True).values("student_id").distinct().count(),
        "pending_count": Submission.objects.filter(assignment__created_by=request.user, score__isnull=True).count(),
        "test_count": SQLTest.objects.filter(created_by=request.user).count(),
        "submissions": submissions,
        "pending_registrations": PortalUser.objects.filter(
            role=PortalUser.Role.STUDENT,
            registration_status=PortalUser.RegistrationStatus.PENDING_ADMIN,
        ).order_by("date_joined")[:50] if request.user.can_approve_students else [],
    }
    return _render_dashboard("teacher-dashboard.html", context, request)


@login_required(login_url="/portal/")
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def teacher_announcements(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    assigned_ids = TeacherAssignment.objects.filter(teacher=request.user).values('class_section_id')
    classes = ClassSection.objects.filter(
        pk__in=assigned_ids, is_active=True,
    ).select_related("course").order_by("course__name", "name", "section")
    class_ids = classes.values_list("id", flat=True)
    student_ids = Enrollment.objects.filter(class_section_id__in=class_ids, active=True).values('student_id')
    students = PortalUser.objects.filter(
        role=PortalUser.Role.STUDENT, is_active=True,
        pk__in=student_ids,
    ).order_by("first_name", "last_name", "username")
    message = ""
    error = ""
    if request.method == "POST":
        title = str(request.POST.get("title", "")).strip()
        body = str(request.POST.get("message", "")).strip()
        audience = request.POST.get("audience", "")
        if not title or len(title) > 200 or not body or len(body) > 10000:
            error = "Enter a title (up to 200 characters) and a message (up to 10,000 characters)."
        else:
            target = {"class_section": None, "student": None}
            if audience == "class":
                target_id = request.POST.get("class_id", "")
                target["class_section"] = classes.filter(pk=int(target_id)).first() if target_id.isdigit() else None
            elif audience == "student":
                target_id = request.POST.get("student_id", "")
                target["student"] = students.filter(pk=int(target_id)).first() if target_id.isdigit() else None
            else:
                error = "Choose a student, class, or all students."
            if not error and not any(target.values()):
                error = "Choose a student or class that belongs to your teaching scope."
            if not error:
                Announcement.objects.create(created_by=request.user, title=title, message=body, **target)
                message = "Announcement published to the selected audience."
    announcements = Announcement.objects.filter(created_by=request.user).filter(
        Q(class_section_id__in=class_ids) | Q(student__in=students)
        | Q(class_section__isnull=True, student__isnull=True)
    ).select_related("class_section", "class_section__course", "student").order_by("-created_at")[:50]
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "dashboard_home_path": _dashboard_path(PortalUser.Role.TEACHER),
        "announcement_classes": classes,
        "announcement_students": students,
        "teacher_announcements": announcements,
        "announcement_message": message,
        "announcement_error": error,
        "is_announcements_page": True,
    }
    return _render_dashboard("teacher-announcements.html", context, request)


@login_required(login_url="/portal/")
def student_dashboard(request):
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    enrollments = Enrollment.objects.filter(student=request.user, active=True).select_related("class_section", "class_section__course")
    enrolled_class_ids = Enrollment.objects.filter(student=request.user, active=True).values("class_section_id")
    enrolled_course_ids = Enrollment.objects.filter(student=request.user, active=True).values("class_section__course_id")
    # Use ID subqueries instead of JOIN + DISTINCT. Oracle cannot apply DISTINCT
    # to rows containing CLOB-backed TextFields such as assignment instructions.
    from .daily_tasks import available_daily_tasks_for_student
    visible_tasks = available_daily_tasks_for_student(request.user)
    assignments = list(visible_tasks.order_by("due_at", "pk")[:6])
    task_submissions = {item.assignment_id: item for item in Submission.objects.filter(student=request.user, assignment__in=visible_tasks)}
    for assignment in assignments:
        assignment.student_submission = task_submissions.get(assignment.pk)
    revision_count = sum(item.review_state == "revision" for item in task_submissions.values())
    completed_count = sum(item.is_correct for item in task_submissions.values())
    announcements = Announcement.objects.filter(published=True).filter(
        Q(class_section__isnull=True, student__isnull=True)
        | Q(student=request.user)
        | Q(class_section_id__in=Subquery(enrolled_class_ids))
    ).order_by("-created_at")[:4]
    shared_code = SharedCompilerCode.objects.filter(published=True).filter(
        Q(class_section__isnull=True, student__isnull=True)
        | Q(student=request.user)
        | Q(class_section_id__in=Subquery(enrolled_class_ids))
        | Q(all_students=True)
    ).select_related("created_by", "class_section", "class_section__course").order_by("-created_at")[:20]
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.STUDENT,
        "dashboard_home_path": _dashboard_path(PortalUser.Role.STUDENT),
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.STUDENT),
        "enrollments": enrollments,
        "course_count": enrollments.values("class_section__course_id").distinct().count(),
        "assignment_count": visible_tasks.count(),
        "revision_count": revision_count, "completed_count": completed_count,
        "test_count": SQLTest.objects.filter(published=True, course_id__in=Subquery(enrolled_course_ids)).count(),
        "assignments": assignments,
        "announcements": announcements,
        "shared_code": shared_code,
    }
    return _render_dashboard("student-dashboard.html", context, request)


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def portal_tests(request):
    role = _active_portal_role(request)
    if role not in (PortalUser.Role.TEACHER, PortalUser.Role.STUDENT):
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    message = ""
    if role == PortalUser.Role.TEACHER:
        teaching_class_ids = TeacherAssignment.objects.filter(teacher=request.user).values("class_section_id")
        teaching_classes = ClassSection.objects.filter(pk__in=teaching_class_ids, is_active=True).select_related("course").order_by("course__name", "name")
        if request.method == "POST":
            if request.POST.get('action') == 'configure_test_sample':
                test = SQLTest.objects.filter(pk=_safe_int(request.POST.get('test_id'),None),created_by=request.user).first()
                if not test:
                    return JsonResponse({'error':'Test not found.'},status=404)
                from .assessment_sandbox import checked_statements
                try:
                    setup = request.POST.get('sample_schema','')
                    checked_statements(setup)
                except ValueError as exc:
                    return JsonResponse({'error':str(exc)},status=400)
                test.sample_schema = setup
                test.save(update_fields=['sample_schema'])
                return redirect('/portal/tests/')
            if request.POST.get("action") == "configure_all_test_answer_input":
                allow_typed = request.POST.get("allow_typed_answer") == "on"
                SQLTest.objects.filter(created_by=request.user).update(allow_typed_answer=allow_typed)
                return redirect("/portal/tests/")
            if request.POST.get("action") == "configure_test_answer_input":
                test_id = request.POST.get("test_id", "")
                with transaction.atomic():
                    test = SQLTest.objects.select_for_update().filter(
                        pk=test_id if test_id.isdigit() else None,
                        created_by=request.user,
                    ).first()
                    if test:
                        test.allow_typed_answer = request.POST.get("allow_typed_answer") == "on"
                        test.save(update_fields=("allow_typed_answer",))
                return redirect("/portal/tests/")
            action = request.POST.get("action")
            if action == "email_single_student_result":
                attempt_id = _safe_int(request.POST.get("attempt_id"), None)
                attempt = TestAttempt.objects.filter(pk=attempt_id, test__created_by=request.user).select_related("student", "test").first()
                if attempt and attempt.student.email:
                    ok = send_test_result_email(attempt.student, attempt.test, attempt, request)
                    message = ("Result email sent to " + attempt.student.email) if ok else "Could not send result email. Check Gmail settings."
                else:
                    message = "Student attempt or email address not found."
            elif action == "email_all_test_results":
                test_id = _safe_int(request.POST.get("test_id"), None)
                test = SQLTest.objects.filter(pk=test_id, created_by=request.user).first()
                if test:
                    count = 0
                    for attempt in test.attempts.filter(submitted_at__isnull=False).select_related("student"):
                        if attempt.student.email and send_test_result_email(attempt.student, test, attempt, request):
                            count += 1
                    message = f"Result emails sent to {count} student(s)."
                else:
                    message = "Test not found."
            elif request.POST.get("action") == "setup_class":
                code = request.POST.get("course_code", "").strip().upper()[:32]
                course_name = request.POST.get("course_name", "").strip()[:160]
                class_name = request.POST.get("new_class_name", "").strip()[:80]
                section = request.POST.get("new_section", "").strip()[:32]
                branch = request.POST.get("new_branch", "").strip()[:80]
                semester = _safe_int(request.POST.get("new_semester"), 0)
                group = request.POST.get("new_group", "").strip()[:32]
                academic_year = request.POST.get("academic_year", "").strip()[:20]
                if not code or not course_name or not class_name or not branch or not section or not group or not 1 <= semester <= 20:
                    message = "Enter a course, class name, branch, semester (1–20), section, and group."
                    tests = SQLTest.objects.filter(created_by=request.user).select_related("class_section", "course").prefetch_related("questions", "attempts__student", "attempts__answers__question").order_by("-id")
                    for test in tests:
                        test.schedule_label = _test_schedule_label(test)
                    return _render_dashboard("portal-tests.html", _tests_context(
                        request, role, tests=tests, teaching_classes=teaching_classes,
                        students=PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username"),
                        message=message,
                    ), request)
                else:
                    with transaction.atomic():
                        course, _ = Course.objects.get_or_create(code=code, defaults={"name": course_name})
                        class_section, _ = ClassSection.objects.get_or_create(
                            course=course, name=class_name, academic_year=academic_year, section=section,
                            branch=branch, semester=semester, group=group,
                        )
                        TeacherAssignment.objects.get_or_create(teacher=request.user, class_section=class_section)
                        student_ids = request.POST.getlist("student_ids")
                        students = PortalUser.objects.filter(pk__in=student_ids, role=PortalUser.Role.STUDENT, is_active=True)
                        for student in students:
                            Enrollment.objects.get_or_create(student=student, class_section=class_section)
                    return redirect("/portal/tests/")
            selected_class_ids = request.POST.getlist("class_sections")
            valid_class_ids = [int(value) for value in selected_class_ids if value.isdigit()]
            class_sections = list(teaching_classes.filter(pk__in=valid_class_ids))
            class_selection_valid = (
                bool(selected_class_ids)
                and len(valid_class_ids) == len(selected_class_ids)
                and len(set(valid_class_ids)) == len(valid_class_ids)
                and len(class_sections) == len(selected_class_ids)
            )
            title = request.POST.get("title", "").strip()[:200]
            posted_questions = request.POST.getlist("question")
            posted_expected_sql = request.POST.getlist("expected_sql")
            questions, expected_sql = [], []
            for index, value in enumerate(posted_questions):
                prompt = value.strip()
                if prompt:
                    questions.append(prompt)
                    expected_sql.append(posted_expected_sql[index].strip() if index < len(posted_expected_sql) else "")
            expected_sql.extend([""] * max(0, len(questions) - len(expected_sql)))
            grading_mode = request.POST.get("grading_mode", SQLTest.GradingMode.MANUAL)
            question_file = request.FILES.get("question_file")
            try:
                if question_file:
                    questions.extend(_questions_from_upload(question_file))
                    if len(questions) > 200:
                        raise ValueError("A test can contain at most 200 questions.")
            except ValueError as error:
                message = str(error)
            frequency = request.POST.get("schedule_frequency", SQLTest.ScheduleFrequency.ANYTIME)
            try:
                schedule_start = time.fromisoformat(request.POST.get("schedule_start_time", "")) if request.POST.get("schedule_start_time") else None
                schedule_end = time.fromisoformat(request.POST.get("schedule_end_time", "")) if request.POST.get("schedule_end_time") else None
                weekdays = sorted({int(day) for day in request.POST.getlist("schedule_weekdays")})
            except (TypeError, ValueError):
                schedule_start, schedule_end, weekdays = None, None, []
                frequency = "invalid"
            if message:
                pass
            elif not class_selection_valid or not title or not questions:
                message = "Select one or more classes, enter a title, and add at least one question."
            elif grading_mode not in {choice for choice, _label in SQLTest.GradingMode.choices}:
                message = "Choose a valid grading method."
            elif grading_mode == SQLTest.GradingMode.RESULT_MATCH and len(questions) > 10:
                message = "Result-based grading supports up to 10 questions per test to keep Oracle execution responsive."
            elif grading_mode != SQLTest.GradingMode.MANUAL and len(expected_sql) < len(questions):
                message = "Add a reference SQL answer for every question when automatic grading is enabled."
            elif grading_mode != SQLTest.GradingMode.MANUAL and any(not value.strip() for value in expected_sql[:len(questions)]):
                message = "Add a reference SQL answer for every question when automatic grading is enabled."
            elif grading_mode == SQLTest.GradingMode.RESULT_MATCH and any(not _is_supported_auto_grade_query(value) for value in expected_sql[:len(questions)]):
                message = "Automatic evaluation requires one valid SELECT, CREATE TABLE, INSERT, UPDATE, or DELETE query per question." 
            elif frequency not in {choice for choice, _label in SQLTest.ScheduleFrequency.choices}:
                message = "Choose a valid test schedule."
            elif frequency != SQLTest.ScheduleFrequency.ANYTIME and (not schedule_start or not schedule_end or schedule_start == schedule_end):
                message = "Set different start and end times for the test schedule."
            elif frequency == SQLTest.ScheduleFrequency.WEEKLY and (not weekdays or any(day < 0 or day > 6 for day in weekdays)):
                message = "Choose at least one weekday for a weekly test."
            else:
                with transaction.atomic():
                    for class_section in class_sections:
                        test = SQLTest.objects.create(
                            course=class_section.course, class_section=class_section,
                            created_by=request.user, title=title,
                            instructions=request.POST.get("instructions", "").strip(),
                            sample_schema=request.POST.get('sample_schema','').strip(),
                            duration_minutes=max(1, min(_safe_int(request.POST.get("duration"), 60), 600)),
                            total_marks=len(questions),
                            published=request.POST.get("published") == "on",
                            schedule_frequency=frequency,
                            schedule_start_time=schedule_start,
                            schedule_end_time=schedule_end,
                            schedule_weekdays=weekdays if frequency == SQLTest.ScheduleFrequency.WEEKLY else [],
                            grading_mode=grading_mode,
                            allow_typed_answer=request.POST.get("allow_typed_answer") == "on",
                        )
                        SQLTestQuestion.objects.bulk_create([
                            SQLTestQuestion(test=test, position=index, prompt=prompt,
                                            expected_sql=expected_sql[index - 1].strip() if grading_mode != SQLTest.GradingMode.MANUAL else "")
                            for index, prompt in enumerate(questions, 1)
                        ])
                if request.POST.get("send_email_notification") in ("on", "1"):
                    _notify_students_about_new_test(request, class_sections, title, len(questions))
                return redirect("/portal/tests/")
        tests = SQLTest.objects.filter(created_by=request.user).select_related("class_section", "course").prefetch_related("questions", "attempts__student", "attempts__answers__question").order_by("-id")
        has_tests = tests.exists()
        all_tests_allow_typed = has_tests and not tests.filter(allow_typed_answer=False).exists()
        for test in tests:
            test.schedule_label = _test_schedule_label(test)
            test.grading_label = dict(SQLTest.GradingMode.choices).get(test.grading_mode, "Teacher review")
        context = _tests_context(request, role, tests=tests, teaching_classes=teaching_classes, students=PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username"), message=message, has_tests=has_tests, all_tests_allow_typed=all_tests_allow_typed)
    else:
        enrollment_classes = Enrollment.objects.filter(student=request.user, active=True).values_list("class_section_id", flat=True)
        tests = SQLTest.objects.filter(published=True).filter(
            Q(class_section_id__in=enrollment_classes) | Q(class_section__isnull=True, course__classes__enrollments__student=request.user, course__classes__enrollments__active=True)
        ).order_by().values("pk")
        tests = SQLTest.objects.filter(pk__in=tests).select_related("class_section", "course").prefetch_related("questions")
        if request.method == "POST":
            test = tests.filter(pk=_safe_int(request.POST.get("test_id"), None)).first()
            now = timezone.now()
            if not test or (test.starts_at and now < test.starts_at) or (test.ends_at and now > test.ends_at) or not _test_schedule_is_open(test, now):
                message = "This test is not currently available."
            else:
                attempt, _ = TestAttempt.objects.get_or_create(test=test, student=request.user)
                if attempt.submitted_at:
                    message = "You have already submitted this test."
                else:
                    questions = list(test.questions.all())
                    submitted_answers = {question.pk: request.POST.get(f"answer_{question.pk}", "").strip() for question in questions}
                    from .assessment_sandbox import executed_test_run
                    try:
                        request._assessment_run = executed_test_run(request.user,test,submitted_answers.values())
                    except ValueError as exc:
                        return JsonResponse({'error':str(exc)},status=409)
                    grades = None
                    grade_error = ""
                    if test.grading_mode != SQLTest.GradingMode.MANUAL:
                        try:
                            grades = _grade_sql_test_answers(request, test, questions, submitted_answers)
                        except Exception as error:
                            grade_error = str(error)[:500]
                    answer_records = []
                    for question in questions:
                        answer_text = submitted_answers[question.pk]
                        grade = (grades or {}).get(question.pk)
                        answer_records.append({
                            "attempt": attempt,
                            "question": question,
                            "defaults": {
                                "answer": answer_text,
                                "is_correct": grade.get("is_correct") if grade else None,
                                "score": grade.get("score") if grade else None,
                                "feedback": grade.get("feedback", "") if grade else (f"Automatic grading pending: {grade_error}" if grade_error else ""),
                            },
                        })
                    for values in answer_records:
                        TestAnswer.objects.update_or_create(
                            attempt=values["attempt"], question=values["question"], defaults=values["defaults"],
                        )
                    graded_answers = [grade for grade in (grades or {}).values()]
                    attempt.score = (sum((grade["score"] for grade in graded_answers), Decimal("0"))
                                     if grades is not None and len(graded_answers) == len(questions) else None)
                    attempt.status = "submitted"
                    attempt.submitted_at = now
                    attempt.save(update_fields=("status", "submitted_at", "score"))
                    send_test_result_email(request.user, test, attempt, request)
                    return redirect("/portal/tests/")
        attempts = {attempt.test_id: attempt for attempt in TestAttempt.objects.filter(student=request.user, test__in=tests).prefetch_related("answers__question")}
        tests = list(tests.order_by("-id"))
        from .daily_tasks import ordered_test_questions
        for test in tests:
            test.student_attempt = attempts.get(test.pk)
            test.student_questions = ordered_test_questions(test, request.user)
            test.grading_label = dict(SQLTest.GradingMode.choices).get(test.grading_mode, "Teacher review")
            test.schedule_open = _test_schedule_is_open(test)
            test.schedule_label = _test_schedule_label(test)
        context = _tests_context(request, role, tests=tests, message=message)
    return _render_dashboard("portal-tests.html", context, request)


def _test_sql_tokens(sql):
    from .sql_lexer import tokens
    return [token for token in tokens(sql) if token["kind"] != "comment"]


def _canonical_test_sql(sql):
    tokens = _test_sql_tokens(sql)
    if tokens and tokens[-1]["text"] == ";":
        tokens.pop()
    canonical = []
    for token in tokens:
        value = token["text"]
        if token["kind"] == "word":
            value = value.upper()
        canonical.append((token["kind"], value))
    return canonical


def _detect_sql_kind(sql):
    tokens = _test_sql_tokens(sql)
    if not tokens:
        return "UNKNOWN"
    first = tokens[0]["text"].upper()
    if first in {"SELECT", "WITH"}:
        return "SELECT"
    if first == "CREATE" and len(tokens) > 1 and tokens[1]["text"].upper() == "TABLE":
        return "CREATE_TABLE"
    if first in {"INSERT", "UPDATE", "DELETE"}:
        return first
    return "OTHER"


def _is_supported_auto_grade_query(sql):
    from .sql_lexer import split_statements
    statements = split_statements(sql)
    if len(statements) != 1:
        return False
    kind = _detect_sql_kind(statements[0])
    if kind == "SELECT":
        return _is_read_only_test_query(statements[0])
    return kind in {"CREATE_TABLE", "INSERT", "UPDATE", "DELETE"}


def _extract_table_name(sql):
    match = re.search(r"CREATE\s+TABLE\s+(?:(?:\w+\.)?[\"\x27\`]?(\w+)[\"\x27\`]?)", sql, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _extract_dml_target_table(sql):
    sql_clean = sql.strip().upper()
    if sql_clean.startswith("INSERT"):
        m = re.search(r"INSERT\s+INTO\s+(?:(?:\w+\.)?[\"\x27\`]?(\w+)[\"\x27\`]?)", sql, re.IGNORECASE)
    elif sql_clean.startswith("UPDATE"):
        m = re.search(r"UPDATE\s+(?:(?:\w+\.)?[\"\x27\`]?(\w+)[\"\x27\`]?)", sql, re.IGNORECASE)
    elif sql_clean.startswith("DELETE"):
        m = re.search(r"DELETE\s+(?:FROM\s+)?(?:(?:\w+\.)?[\"\x27\`]?(\w+)[\"\x27\`]?)", sql, re.IGNORECASE)
    else:
        m = None
    return m.group(1).upper() if m else None


def _extract_expected_columns(sql):
    match = re.search(r"CREATE\s+TABLE\s+.*?[\"\x27\`]?\w+[\"\x27\`]?\s*\((.*)\)", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    body = match.group(1)
    parts, depth, cur = [], 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(char)
    if cur:
        parts.append("".join(cur).strip())
    cols = []
    for part in parts:
        clean = re.sub(r"\s+", " ", part).strip()
        tokens = clean.split(" ")
        if not tokens or tokens[0].upper() in ("CONSTRAINT", "PRIMARY", "FOREIGN", "CHECK", "UNIQUE"):
            continue
        col_name = tokens[0].strip("\"'\x27`").upper()
        if col_name:
            cols.append(col_name)
    return cols


def _is_read_only_test_query(sql):
    from .sql_lexer import bind_names, split_statements
    statements = split_statements(sql)
    if len(statements) != 1 or bind_names(sql):
        return False
    tokens = _test_sql_tokens(statements[0])
    if not tokens or tokens[0]["text"].upper() not in {"SELECT", "WITH"}:
        return False
    words = [token["text"].upper() for token in tokens if token["kind"] == "word"]
    if any(word in {"INSERT", "UPDATE", "DELETE", "MERGE", "COMMIT", "ROLLBACK", "CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT", "REVOKE", "EXECUTE", "CALL", "BEGIN", "DECLARE"} for word in words):
        return False
    return not any(left == "FOR" and right == "UPDATE" for left, right in zip(words, words[1:]))


def _test_oracle_settings(request):
    run = getattr(request, '_assessment_run', None)
    if run is not None:
        from .models import _portal_secret_cipher
        if run.owner_id != request.user.pk or run.schema_removed_at or not run.schema_password_encrypted:
            raise ValueError('The assessment sandbox is unavailable.')
        return {'user': run.schema_name,
            'password': _portal_secret_cipher().decrypt(run.schema_password_encrypted.encode()).decode(),
            'connectString': run.sandbox_connection.connect_string}
    from .views import current_oracle_session
    compiler_session = current_oracle_session(request, touch=False)
    if compiler_session and compiler_session.get("settings"):
        return dict(compiler_session["settings"])
    user = request.user
    if user.has_oracle_credentials():
        password = user.get_oracle_password()
        if password:
            return {"user": user.oracle_username, "password": password, "connectString": user.oracle_connect_string}
    raise ValueError("Connect to Oracle and save your credentials in the compiler before submitting this result-graded test.")


def _test_query_result(connection, sql):
    from .sql_lexer import prepare_statement
    with connection.cursor() as cursor:
        cursor.arraysize = 501
        connection.call_timeout = 2000
        cursor.execute(prepare_statement(sql))
        if not cursor.description:
            raise ValueError("Reference SQL must return a result set.")
        column_names = [col[0].upper() for col in cursor.description]
        rows = cursor.fetchmany(501)
    if len(rows) > 500:
        raise ValueError("A graded query may return at most 500 rows.")

    def normalize(value):
        if value is None:
            return ("null", "")
        if isinstance(value, Decimal):
            return ("number", format(value.normalize(), "f"))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return ("number", format(Decimal(str(value)).normalize(), "f"))
        if isinstance(value, (date, time)):
            return (type(value).__name__, value.isoformat())
        if hasattr(value, "read"):
            raise ValueError("Large object columns are not supported by automatic result grading.")
        if isinstance(value, bytes):
            return ("bytes", value.hex())
        return (type(value).__name__, str(value))

    normalized_rows = [tuple(normalize(value) for value in row) for row in rows]
    words = [token["text"].upper() for token in _test_sql_tokens(sql) if token["kind"] == "word"]
    ordered = any(a == "ORDER" and b == "BY" for a, b in zip(words, words[1:]))
    encoded_rows = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in normalized_rows]
    return column_names, encoded_rows, ordered


def _evaluate_select_query(connection, expected_sql, candidate_sql, marks):
    expected_cols, expected_rows, ordered = _test_query_result(connection, expected_sql)
    try:
        actual_cols, actual_rows, _ = _test_query_result(connection, candidate_sql)
    except Exception as error:
        if re.search(r"\b(?:DPY-(?:1001|4011)|ORA-(?:01013|03113|03114|03135|12537|12547|12570|12571))\b", str(error), re.I):
            raise
        return {"is_correct": False, "score": Decimal("0"), "feedback": f"Oracle could not run this query: {str(error)[:300]}"}

    columns_correct = (actual_cols == expected_cols)
    if ordered:
        rows_correct = (actual_rows == expected_rows)
    else:
        rows_correct = (Counter(actual_rows) == Counter(expected_rows))

    passed = columns_correct and rows_correct
    if passed:
        feedback = "Query results match the reference."
    elif not columns_correct and rows_correct:
        feedback = "Rows match, but column names or column order do not match the reference."
    elif columns_correct and not rows_correct:
        feedback = f"Columns match, but returned rows differ (expected {len(expected_rows)} rows, got {len(actual_rows)} rows)."
    else:
        feedback = "Query ran, but its result rows and columns do not match the reference."

    return {
        "is_correct": passed,
        "score": marks if passed else Decimal("0"),
        "feedback": feedback,
    }


def _evaluate_create_table(connection, expected_sql, candidate_sql, marks):
    expected_table = _extract_table_name(expected_sql)
    candidate_table = _extract_table_name(candidate_sql)
    if not expected_table:
        return {"is_correct": False, "score": Decimal("0"), "feedback": "Invalid reference CREATE TABLE statement."}
    if candidate_table and candidate_table != expected_table:
        return {"is_correct": False, "score": Decimal("0"), "feedback": f"Expected table '{expected_table}', but statement creates '{candidate_table}'."}

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT column_name, data_type, nullable
            FROM user_tab_columns
            WHERE table_name = :tbl
            ORDER BY column_id
        """, {'tbl': expected_table})
        actual_cols = cursor.fetchall()
        if not actual_cols:
            return {"is_correct": False, "score": Decimal("0"), "feedback": f"Table '{expected_table}' was not found in schema. Create the table successfully first."}

        cursor.execute("""
            SELECT c.constraint_type, cc.column_name
            FROM user_constraints c
            JOIN user_cons_columns cc ON c.constraint_name = cc.constraint_name AND c.owner = cc.owner
            WHERE c.table_name = :tbl
        """, {'tbl': expected_table})
        constraints = cursor.fetchall()
        actual_pks = {col.upper() for ctype, col in constraints if ctype == 'P'}

    expected_cols = _extract_expected_columns(expected_sql)
    actual_col_names = [col[0].upper() for col in actual_cols]
    missing_cols = [col for col in expected_cols if col not in actual_col_names]
    if missing_cols:
        return {"is_correct": False, "score": Decimal("0"), "feedback": f"Table created, but missing column(s): {', '.join(missing_cols)}."}

    if 'PRIMARY KEY' in expected_sql.upper() and not actual_pks:
        return {"is_correct": False, "score": Decimal("0"), "feedback": f"Table '{expected_table}' created, but missing the required PRIMARY KEY constraint."}

    return {"is_correct": True, "score": marks, "feedback": f"Table '{expected_table}' created and verified successfully."}


def _evaluate_dml_statement(connection, expected_sql, candidate_sql, marks):
    expected_target = _extract_dml_target_table(expected_sql)
    candidate_target = _extract_dml_target_table(candidate_sql)
    if not expected_target:
        return {"is_correct": False, "score": Decimal("0"), "feedback": "Invalid reference DML statement."}
    if candidate_target and candidate_target != expected_target:
        return {"is_correct": False, "score": Decimal("0"), "feedback": f"Expected DML to target table '{expected_target}', but got '{candidate_target}'."}

    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {expected_target}")
            count = cursor.fetchone()[0]
    except Exception as error:
        return {"is_correct": False, "score": Decimal("0"), "feedback": f"Table '{expected_target}' evaluation error: {str(error)[:300]}"}

    return {"is_correct": True, "score": marks, "feedback": f"DML statement executed successfully on '{expected_target}'."}






def send_test_result_email(student, test, attempt, request=None):
    """Send formatted scorecard email to student with marks and question breakdown."""
    try:
        from .models import PortalSetting
        from .otp_delivery import send_account_email
        setting = PortalSetting.objects.first()
        if not setting or not setting.gmail_login_email or not setting.get_gmail_app_password():
            logger.warning("Result email skipped: Gmail not configured in PortalSetting.")
            return False

        if not student.email:
            return False

        answers = attempt.answers.select_related("question").all()
        total_marks = test.total_marks
        score = attempt.score if attempt.score is not None else Decimal("0")

        breakdown = []
        for ans in answers:
            status = "Correct" if ans.is_correct else ("Incorrect" if ans.is_correct is False else "Pending Review")
            ans_score = ans.score if ans.score is not None else Decimal("0")
            q_marks = ans.question.marks
            fb = f"\n   Feedback: {ans.feedback}" if ans.feedback else ""
            breakdown.append(f"Q{ans.question.position}. {ans.question.prompt}\n   Status: {status} ({ans_score} / {q_marks} marks){fb}")

        breakdown_text = "\n\n".join(breakdown) if breakdown else "No individual questions recorded."
        portal_url = request.build_absolute_uri("/portal/tests/") if request else "https://your-portal-url/portal/tests/"
        recipient_name = student.get_full_name() or student.username

        subject = f"Test Result: {test.title} (Score: {score}/{total_marks})"
        body = (
            f"Dear {recipient_name},\n\n"
            f"Your result for '{test.title}' is ready.\n\n"
            f"========================================\n"
            f"SCORECARD:\n"
            f"- Total Score: {score} / {total_marks}\n"
            f"- Submission Time: {attempt.submitted_at.strftime('%Y-%m-%d %H:%M') if attempt.submitted_at else 'N/A'}\n"
            f"- Status: {attempt.status.title()}\n"
            f"========================================\n\n"
            f"Question-by-Question Breakdown:\n"
            f"{breakdown_text}\n\n"
            f"Review your complete test answers and sandbox reports here:\n"
            f"{portal_url}\n\n"
            f"Best regards,\n"
            f"Oracle SQL Learning Portal"
        )
        send_account_email(setting, student.email, subject, body)
        return True
    except Exception as exc:
        logger.exception("Failed to send test result email to %s: %s", getattr(student, 'email', ''), exc)
        return False

def _notify_students_about_new_test(request, class_sections, test_title, question_count):
    """Notify all active enrolled students in the target classes via Gmail SMTP."""
    try:
        from .models import PortalSetting, PortalUser, Enrollment
        from .otp_delivery import send_account_email
        setting = PortalSetting.objects.first()
        if not setting or not setting.gmail_login_email or not setting.get_gmail_app_password():
            logger.warning("Email notification skipped: Gmail settings not configured in PortalSetting.")
            return

        class_section_ids = [cs.pk for cs in class_sections]
        enrolled_student_ids = Enrollment.objects.filter(
            class_section_id__in=class_section_ids,
            active=True,
            student__is_active=True,
            student__role=PortalUser.Role.STUDENT
        ).values_list('student_id', flat=True).distinct()

        students = PortalUser.objects.filter(pk__in=enrolled_student_ids).exclude(email='').distinct()
        if not students.exists():
            return

        portal_url = request.build_absolute_uri('/portal/tests/')
        teacher_name = request.user.get_full_name() or request.user.username
        subject = f"New Test Assigned: {test_title}"

        for student in students:
            recipient_name = student.get_full_name() or student.username
            body = (
                f"Hello {recipient_name},\n\n"
                f"A new SQL Test has been uploaded for your class by {teacher_name}.\n\n"
                f"Test Title: {test_title}\n"
                f"Total Questions: {question_count}\n\n"
                f"Please log in to your portal to take the test:\n"
                f"{portal_url}\n\n"
                f"Best regards,\n"
                f"Oracle Learning Portal"
            )
            try:
                send_account_email(setting, student.email, subject, body)
            except Exception as exc:
                logger.warning("Failed to send test email to %s: %s", student.email, exc)
    except Exception as exc:
        logger.exception("Error in _notify_students_about_new_test: %s", exc)

def _grade_sql_test_answers(request, test, questions, submitted_answers):
    from .models import SQLTest
    mode = test.grading_mode
    if mode == SQLTest.GradingMode.SQL_MATCH:
        grades = {}
        for question in questions:
            correct = bool(submitted_answers.get(question.pk, "").strip()) and (
                _canonical_test_sql(submitted_answers[question.pk]) == _canonical_test_sql(question.expected_sql)
            )
            grades[question.pk] = {
                "is_correct": correct,
                "score": question.marks if correct else Decimal("0"),
                "feedback": "SQL statement matches the reference." if correct else "The SQL statement does not match the reference answer.",
            }
        return grades
    if mode != SQLTest.GradingMode.RESULT_MATCH:
        return {}
    from .views import connect_options, open_oracle_connection
    connection = None
    try:
        connection = open_oracle_connection(connect_options(_test_oracle_settings(request)))
        connection.call_timeout = 2000
        grades = {}
        for question in questions:
            candidate = submitted_answers.get(question.pk, "").strip()
            expected = (question.expected_sql or "").strip()
            if not candidate:
                grades[question.pk] = {"is_correct": False, "score": Decimal("0"), "feedback": "No answer provided."}
                continue

            kind = _detect_sql_kind(expected)
            if kind == 'SELECT':
                if not _is_read_only_test_query(candidate):
                    grades[question.pk] = {"is_correct": False, "score": Decimal("0"), "feedback": "Enter one read-only SELECT query without bind variables."}
                    continue
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                except Exception:
                    pass
                grades[question.pk] = _evaluate_select_query(connection, expected, candidate, question.marks)
                try:
                    connection.rollback()
                except Exception:
                    pass
            elif kind == 'CREATE_TABLE':
                grades[question.pk] = _evaluate_create_table(connection, expected, candidate, question.marks)
            elif kind in {'INSERT', 'UPDATE', 'DELETE'}:
                grades[question.pk] = _evaluate_dml_statement(connection, expected, candidate, question.marks)
            else:
                grades[question.pk] = _evaluate_select_query(connection, expected, candidate, question.marks)
        return grades
    finally:
        if connection:
            try: connection.rollback()
            except Exception: pass
            try: connection.close()
            except Exception: pass


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _questions_from_upload(upload):
    if upload.size > 2 * 1024 * 1024:
        raise ValueError("Question files must be 2 MB or smaller.")
    suffix = Path(upload.name or "").suffix.lower()
    try:
        if suffix == ".txt":
            content = upload.read().decode("utf-8-sig")
            rows = content.splitlines()
        elif suffix == ".csv":
            content = upload.read().decode("utf-8-sig")
            rows = []
            for row in csv.reader(io.StringIO(content)):
                if row and row[0].strip():
                    first_cell = row[0].strip()
                    if not rows and first_cell.lower() in {"question", "questions", "prompt"}:
                        continue
                    rows.append(first_cell)
        elif suffix == ".docx":
            with zipfile.ZipFile(upload) as docx:
                document = docx.getinfo("word/document.xml")
                if document.file_size > 4 * 1024 * 1024:
                    raise ValueError("The document contains too much text to import.")
                root = ElementTree.fromstring(docx.read(document))
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            rows = ["".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip() for paragraph in root.findall(".//w:body/w:p", namespace)]
        else:
            raise ValueError("Use a .txt, .csv, or .docx question file.")
    except (UnicodeDecodeError, csv.Error, zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, OSError):
        raise ValueError("The question file could not be read. Check its format and try again.") from None
    return _questions_from_rows(rows)


def _questions_from_text(text):
    """Use the file importer's question boundaries for pasted text."""
    return _questions_from_rows(text.splitlines())


def _questions_from_rows(rows):
    start_pattern = re.compile(
        r"^\s*(?:(?:(?:question|q)\s*)?\d+\s*(?:[.):\-]|\s+)|[-#]+\s*|/\*\s*)",
        re.I,
    )
    number_pattern = re.compile(r"^(?:(?:question|q)\s*)?\d+\s*(?:[.):\-]|\s+)\s*", re.I)
    lines = [str(row).strip() for row in rows]
    lines = [line for line in lines if line]
    if not any(start_pattern.match(line) for line in lines):
        questions = lines
    else:
        questions, preamble = [], []
        current = None
        in_block_comment = False

        def append_question(parts):
            text = "\n".join(part for part in parts if part).strip()
            if text and text.casefold().rstrip(":") not in {"questions", "question list", "questions list"}:
                questions.append(text)

        for line in lines:
            marker = start_pattern.match(line)
            if marker:
                if current is not None:
                    append_question(current)
                elif preamble:
                    append_question(preamble)
                    preamble = []
                current = []
                body = line[marker.end():].strip()
                body = number_pattern.sub("", body, count=1).strip()
                in_block_comment = line.lstrip().startswith("/*")
                if in_block_comment and body.endswith("*/"):
                    body = body[:-2].rstrip()
                    in_block_comment = False
                if body:
                    current.append(body)
            elif current is None:
                preamble.append(line)
            else:
                continuation = line
                if in_block_comment and continuation.endswith("*/"):
                    continuation = continuation[:-2].rstrip()
                    in_block_comment = False
                if continuation:
                    current.append(continuation)
        if current is not None:
            append_question(current)
        elif preamble:
            append_question(preamble)
    if not questions:
        raise ValueError("No questions were found in that file.")
    if len(questions) > 200:
        raise ValueError("A test can contain at most 200 questions.")
    return questions


def _test_schedule_is_open(test, now=None):
    if test.schedule_frequency == SQLTest.ScheduleFrequency.ANYTIME:
        return True
    if not test.schedule_start_time or not test.schedule_end_time:
        return False
    local_now = timezone.localtime(now or timezone.now())
    current_time = local_now.time().replace(tzinfo=None)
    start, end = test.schedule_start_time, test.schedule_end_time
    if test.schedule_frequency == SQLTest.ScheduleFrequency.WEEKLY:
        schedule_day = (local_now.weekday() - 1) % 7 if start > end and current_time < end else local_now.weekday()
        if schedule_day not in (test.schedule_weekdays or []):
            return False
    elif test.schedule_frequency != SQLTest.ScheduleFrequency.DAILY:
        return False
    return start <= current_time < end if start < end else current_time >= start or current_time < end


def _test_schedule_label(test):
    if test.schedule_frequency == SQLTest.ScheduleFrequency.ANYTIME:
        return "Available anytime"
    if not test.schedule_start_time or not test.schedule_end_time:
        return "Schedule not configured"
    times = f"{test.schedule_start_time.strftime('%H:%M')}–{test.schedule_end_time.strftime('%H:%M')}"
    if test.schedule_frequency == SQLTest.ScheduleFrequency.DAILY:
        return f"Daily · {times}"
    labels = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    days = ", ".join(labels[day] for day in (test.schedule_weekdays or []) if isinstance(day, int) and 0 <= day <= 6)
    return f"Weekly · {days or 'No days selected'} · {times}"


def _tests_context(request, role, **values):
    context = {
        "user": request.user,
        "portal_role": role,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(role),
        "is_tests_page": True,
        "dashboard_home_path": _dashboard_path(role),
    }
    context.update(values)
    return context


@login_required(login_url="/portal/")
@require_GET
def teacher_activity_dashboard(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    can_view_all_activity = request.user.can_view_all_compiler_activity
    assignments = TeacherAssignment.objects.filter(teacher=request.user).select_related(
        "class_section", "class_section__course", "class_subject__subject"
    ).order_by("class_section__course__name", "class_section__name", "class_subject__subject__code")
    activity_classes = ClassSection.objects.all() if can_view_all_activity else ClassSection.objects.filter(teacher_assignments__teacher=request.user).distinct()
    activity_classes = activity_classes.select_related("course").order_by("course__name", "name", "section")
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "activity_assignments": assignments,
        "activity_classes": activity_classes,
        "can_view_all_compiler_activity": can_view_all_activity,
        "is_activity_page": True,
    }
    return _render_dashboard("teacher-activity.html", context, request)


@login_required(login_url="/portal/")
@require_GET
def teacher_attendance_dashboard(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    can_view_all = request.user.can_view_all_compiler_activity
    classes = ClassSection.objects.all() if can_view_all else ClassSection.objects.filter(teacher_assignments__teacher=request.user).distinct()
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "attendance_classes": classes.select_related("course").order_by("course__name", "name", "section"),
        "attendance_assignments": TeacherAssignment.objects.filter(class_section_id__in=classes.values("id")).select_related("class_subject__subject").order_by("class_subject__subject__name"),
        "can_view_all_compiler_activity": can_view_all,
        "dashboard_home_path": _dashboard_path(PortalUser.Role.TEACHER),
        "is_attendance_page": True,
    }
    return _render_dashboard("teacher-attendance.html", context, request)


@login_required(login_url="/portal/")
@require_GET
def teacher_history_dashboard(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "dashboard_home_path": _dashboard_path(PortalUser.Role.TEACHER),
        "is_history_page": True,
    }
    return _render_dashboard("teacher-history.html", context, request)


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def teacher_remote_code_dashboard(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return redirect("/portal/choose-role/" if len(_available_portal_roles(request.user)) > 1 else _dashboard_path(request.user.role))
    if request.user.can_view_all_compiler_activity:
        students = PortalUser.objects.filter(role=PortalUser.Role.STUDENT)
    else:
        enrolled_ids = Enrollment.objects.filter(
            class_section__teacher_assignments__teacher=request.user,
            student__role=PortalUser.Role.STUDENT,
        ).values_list("student_id", flat=True)
        students = PortalUser.objects.filter(pk__in=enrolled_ids, role=PortalUser.Role.STUDENT)
    # Deduplicate IDs before loading related objects: Oracle cannot DISTINCT NCLOBs.
    class_ids = ClassSection.objects.filter(teacher_assignments__teacher=request.user, is_active=True).order_by().values("pk")
    classes = ClassSection.objects.filter(pk__in=class_ids).select_related("course").order_by("course__name", "name")
    remote_students = students.order_by("first_name", "last_name", "username")
    share_student_ids = PortalUser.objects.filter(
        role=PortalUser.Role.STUDENT, is_active=True,
        enrollments__active=True, enrollments__class_section__in=classes,
    ).order_by().values("pk")
    share_students = PortalUser.objects.filter(pk__in=share_student_ids).order_by("first_name", "last_name", "username")
    share_code_message = ""
    share_code_error = ""
    if request.method == "POST":
        title = str(request.POST.get("code_title", "")).strip()
        code = str(request.POST.get("code", ""))
        editor_kind = request.POST.get("editor_kind", "")
        audience = request.POST.get("code_audience", "")
        target = {"class_section": None, "student": None}
        if audience == "all":
            if not PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).exists():
                share_code_error = "There are no active student accounts to receive this code."
        elif audience == "class":
            target_id = request.POST.get("code_class_id", "")
            target["class_section"] = classes.filter(pk=int(target_id)).first() if target_id.isdigit() else None
        elif audience == "student":
            target_id = request.POST.get("code_student_id", "")
            target["student"] = share_students.filter(pk=int(target_id)).first() if target_id.isdigit() else None
        else:
            share_code_error = "Choose a student, class, or lab audience."
        if not title or len(title) > 200 or not code.strip() or len(code) > 50000:
            share_code_error = "Enter a title and code (up to 50,000 characters)."
        elif audience != "all" and not share_code_error and not any(target.values()):
            share_code_error = "Choose a student, class, or lab from your teaching scope."
        if not share_code_error and editor_kind not in SharedCompilerCode.EditorKind.values:
            share_code_error = "Choose SQLCL or SQL WorkSheet."
        if not share_code_error:
            SharedCompilerCode.objects.create(created_by=request.user, title=title, code=code, editor_kind=editor_kind, all_students=audience == "all", **target)
            share_code_message = "Code shared with all active students." if audience == "all" else "Code shared. Students can view and copy it from their portal."
    shared_code_records = SharedCompilerCode.objects.filter(created_by=request.user).filter(
        Q(class_section__in=classes) | Q(student__in=share_students) | Q(all_students=True)
    ).select_related("class_section", "class_section__course", "student").order_by("-created_at")[:30]
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "dashboard_home_path": _dashboard_path(PortalUser.Role.TEACHER),
        "remote_code_students": remote_students,
        "share_code_students": share_students,
        "remote_code_classes": classes,
        "shared_code_records": shared_code_records,
        "share_code_message": share_code_message,
        "share_code_error": share_code_error,
        "is_remote_code_page": True,
    }
    return _render_dashboard("teacher-remote-code.html", context, request)


@login_required(login_url="/portal/")
@require_POST
def compiler_activity_heartbeat(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Activity updates must come from this portal."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Activity update could not be read."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Activity update is invalid."}, status=400)

    attendance_id = request.session.get("attendance_session_id")
    attendance = AttendanceSession.objects.filter(pk=attendance_id, user=request.user).first() if attendance_id else None
    if attendance and attendance.logout_at and attendance.logout_estimated:
        attendance.logout_at = None
        attendance.logout_estimated = False
        attendance.end_reason = ""
    if not attendance or attendance.logout_at:
        attendance = AttendanceSession.objects.create(user=request.user, source="recovered")
        request.session["attendance_session_id"] = attendance.pk

    previous_context = attendance.class_section_id
    now = timezone.now()
    page = "compiler" if payload.get("page") == "compiler" else "portal"
    active = payload.get("active") is True

    elapsed = max(0, min(int((now - attendance.last_heartbeat_at).total_seconds()), 45))
    if elapsed:
        if active: attendance.active_seconds += elapsed
        else: attendance.idle_seconds += elapsed
    previous_slice = attendance.activity_slices.filter(ended_at__isnull=True).order_by("-started_at", "-id").first()
    context_changed = previous_context != attendance.class_section_id
    if previous_slice and context_changed:
        previous_slice.ended_at = previous_slice.last_heartbeat_at
        previous_slice.is_active_now = False
        previous_slice.compiler_active = False
        previous_slice.save(update_fields=("ended_at", "is_active_now", "compiler_active"))
        previous_slice = None
    if not previous_slice:
        current_slice = CompilerActivitySlice.objects.create(
            attendance_session=attendance, class_section=attendance.class_section,
            started_at=now, last_heartbeat_at=now, last_activity_at=now if active else attendance.last_activity_at,
            current_page=page, is_active_now=active, compiler_active=page == "compiler" and active,
        )
    else:
        slice_elapsed = max(0, min(int((now - previous_slice.last_heartbeat_at).total_seconds()), 45))
        if slice_elapsed:
            if active: previous_slice.active_seconds += slice_elapsed
            else: previous_slice.idle_seconds += slice_elapsed
        previous_slice.last_heartbeat_at = now
        previous_slice.current_page = page
        previous_slice.is_active_now = active
        previous_slice.compiler_active = page == "compiler" and active
        if active: previous_slice.last_activity_at = now
        previous_slice.save(update_fields=("active_seconds", "idle_seconds", "last_heartbeat_at", "current_page", "is_active_now", "compiler_active", "last_activity_at"))
    attendance.last_heartbeat_at = now
    attendance.current_page = page
    attendance.is_active_now = active
    attendance.compiler_active = page == "compiler" and active
    if active: attendance.last_activity_at = now
    attendance.save(update_fields=(
        "class_section", "active_seconds", "idle_seconds", "last_heartbeat_at",
        "current_page", "is_active_now", "compiler_active", "last_activity_at",
        "logout_at", "logout_estimated", "end_reason",
    ))
    request.student_attendance = attendance
    from .views import _lock, _oracle_sessions, _refresh_compiler_session_cookie, _sync_independent_compiler_expiry
    with _lock:
        compiler_session = _oracle_sessions.get(request.session.session_key)
        if compiler_session and compiler_session.get("independent_enabled"):
            _sync_independent_compiler_expiry(request, compiler_session)
            request._compiler_session_cookie = compiler_session.get("independent_token", "")
    return _refresh_compiler_session_cookie(JsonResponse({"ok": True, "serverTime": now.isoformat()}), request)


@login_required(login_url="/portal/")
@require_GET
def teacher_compiler_activity_student(request, student_id):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return JsonResponse({"error": "Only teachers can view student activity."}, status=403)
    can_view_all_activity = request.user.can_view_all_compiler_activity
    class_ids = list(ClassSection.objects.values_list("id", flat=True)) if can_view_all_activity else list(TeacherAssignment.objects.filter(teacher=request.user).values_list("class_section_id", flat=True))
    student_query = PortalUser.objects.filter(pk=student_id, role=PortalUser.Role.STUDENT, is_active=True)
    if not can_view_all_activity:
        student_query = student_query.filter(enrollments__active=True, enrollments__class_section_id__in=class_ids)
    student = student_query.distinct().first()
    if not student:
        return JsonResponse({"error": "Student is not available in your activity scope."}, status=403)
    try:
        report_day = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return JsonResponse({"error": "Choose a valid date."}, status=400)
    class_id = request.GET.get("class", "")
    class_filter = None
    if class_id:
        try:
            class_filter = ClassSection.objects.filter(pk=int(class_id), pk__in=class_ids).first()
        except ValueError:
            return JsonResponse({"error": "Invalid class filter."}, status=400)
        if not class_filter or not student.enrollments.filter(class_section=class_filter, active=True).exists():
            return JsonResponse({"error": "Class is not assigned to this student."}, status=403)
    sessions = AttendanceSession.objects.filter(user=student, login_at__date=report_day).order_by("login_at")
    activity_slices = CompilerActivitySlice.objects.filter(attendance_session__user=student, started_at__date=report_day).select_related("class_section")
    events = CompilerExecutionEvent.objects.filter(student=student, executed_at__date=report_day).select_related("class_section")
    paste_events = CompilerPasteEvent.objects.filter(student=student, created_at__date=report_day).select_related("attendance_session")
    client_events = CompilerClientActivityEvent.objects.filter(student=student, created_at__date=report_day)
    if class_filter:
        sessions = sessions.filter(Q(class_section=class_filter) | Q(class_section__isnull=True))
        activity_slices = activity_slices.filter(class_section=class_filter)
        events = events.filter(Q(class_section=class_filter) | Q(attendance_session__class_section=class_filter))
        paste_events = paste_events.filter(attendance_session__class_section=class_filter)
        client_events = client_events.filter(attendance_session__class_section=class_filter)
    timeline = []
    for session in sessions:
        timeline.append({"at": session.login_at.isoformat(), "title": "Login · in-time", "detail": f"{session.source} session started.", "kind": "login"})
        if session.last_activity_at and session.last_activity_at > session.login_at:
            timeline.append({"at": session.last_activity_at.isoformat(), "title": "Last active", "detail": f"{session.active_seconds // 60} min active · {session.idle_seconds // 60} min idle.", "kind": "activity"})
        if session.logout_at:
            timeline.append({"at": session.logout_at.isoformat(), "title": "Out-time", "detail": "Estimated from last heartbeat." if session.logout_estimated else "Confirmed at logout.", "kind": "logout", "estimated": session.logout_estimated})
    for activity_slice in activity_slices:
        context_name = str(activity_slice.class_section) if activity_slice.class_section else "Compiler session"
        timeline.append({"at": activity_slice.last_heartbeat_at.isoformat(), "title": f"Activity · {context_name}", "detail": f"{activity_slice.active_seconds // 60} min active · {activity_slice.idle_seconds // 60} min idle", "kind": "activity"})
    for event in events:
        detail = f"{event.duration_ms} ms · {event.row_count} rows"
        if event.typed_chars is not None:
            detail += f" · typed {event.typed_chars} · pasted {event.pasted_chars or 0} · paste actions {event.paste_count or 0} · edits after paste {event.edits_after_paste or 0}"
        timeline.append({"at": event.executed_at.isoformat(), "title": f"{event.source} · {'Successful' if event.status == 'success' else 'Failed'}", "detail": detail, "kind": "execution"})
    for event in paste_events.order_by("-created_at")[:500]:
        pasted_character_count = f"at least {len(event.text)}" if event.truncated else str(len(event.text))
        timeline.append({
            "at": event.created_at.isoformat(), "title": f"Pasted into {event.source} editor",
            "detail": f"{pasted_character_count} characters pasted. The browser does not expose which app or tab supplied the clipboard text." + (" Preview is limited to the first 4,000 characters." if len(event.text) > 4000 else "") + (" Paste text was truncated for storage." if event.truncated else ""),
            "kind": "paste", "preview": event.text[:4000], "truncated": event.truncated,
        })
    event_titles = {
        CompilerClientActivityEvent.Kind.PAGE_HIDDEN: ("Compiler tab became hidden", "The page became hidden; this can mean another tab, window, or app was brought forward."),
        CompilerClientActivityEvent.Kind.PAGE_VISIBLE: ("Compiler tab became visible", "The compiler page became visible again."),
        CompilerClientActivityEvent.Kind.WINDOW_BLUR: ("Browser window lost focus", "The browser window reported losing focus; the destination app or window is not available."),
        CompilerClientActivityEvent.Kind.WINDOW_FOCUS: ("Browser window regained focus", "The browser window reported focus again."),
    }
    for event in client_events.order_by("-created_at")[:1000]:
        title, detail = event_titles[event.kind]
        timeline.append({"at": event.created_at.isoformat(), "title": title, "detail": f"{detail} · {event.source}", "kind": "focus"})
    timeline.sort(key=lambda event: event["at"])
    return JsonResponse({"student": student.get_full_name() or student.username, "date": report_day.isoformat(), "events": timeline})


@login_required(login_url="/portal/")
@require_GET
def teacher_attendance_report(request):
    """Build an attendance register from the durable portal login sessions."""
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return JsonResponse({"error": "Only teachers can view student attendance."}, status=403)
    can_view_all = request.user.can_view_all_compiler_activity
    class_ids = list(ClassSection.objects.values_list("id", flat=True)) if can_view_all else list(
        TeacherAssignment.objects.filter(teacher=request.user).values_list("class_section_id", flat=True)
    )
    if not class_ids and not can_view_all:
        return JsonResponse({"students": [], "eligibleDays": 0, "from": "", "to": ""})

    try:
        end_day = date.fromisoformat(request.GET.get("to", "")) if request.GET.get("to") else timezone.localdate()
        start_day = date.fromisoformat(request.GET.get("from", "")) if request.GET.get("from") else end_day - timedelta(days=29)
        selected_class_id = int(request.GET["class"]) if request.GET.get("class") else None
    except (ValueError, TypeError):
        return JsonResponse({"error": "Choose valid attendance dates and filters."}, status=400)
    if start_day > end_day or (end_day - start_day).days > 365:
        return JsonResponse({"error": "Choose a date range of up to 366 days."}, status=400)
    if selected_class_id and selected_class_id not in class_ids:
        return JsonResponse({"error": "Class is not assigned to you."}, status=403)

    class_query = ClassSection.objects.filter(pk__in=class_ids)
    if selected_class_id:
        class_query = class_query.filter(pk=selected_class_id)

    enrollments = Enrollment.objects.filter(
        class_section__in=class_query, active=True, student__role=PortalUser.Role.STUDENT, student__is_active=True,
    ).select_related("student").order_by("student__first_name", "student__last_name", "student__username")
    class_section = class_query.first() if selected_class_id else None
    scheduled_start = class_section.attendance_start_time if class_section else None
    students = {}
    for enrollment in enrollments:
        students.setdefault(enrollment.student_id, {"user": enrollment.student, "presentDates": set(), "sessions": []})
    if can_view_all and not selected_class_id:
        for student in PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username"):
            students.setdefault(student.pk, {"user": student, "presentDates": set(), "sessions": []})
    student_ids = list(students)
    sessions = AttendanceSession.objects.filter(user_id__in=student_ids).filter(
        Q(login_at__date__gte=start_day, login_at__date__lte=end_day)
        | Q(logout_at__date__gte=start_day, logout_at__date__lte=end_day)
        | Q(login_at__date__lte=end_day, logout_at__date__gte=start_day)
        | Q(login_at__date__lte=end_day, logout_at__isnull=True, last_heartbeat_at__date__gte=start_day)
    )
    for session in sessions.order_by("login_at"):
        login_day = timezone.localtime(session.login_at).date()
        students[session.user_id]["sessions"].append(session)
        if start_day <= login_day <= end_day and login_day.weekday() < 5:
            students[session.user_id]["presentDates"].add(login_day.isoformat())
        if session.logout_at:
            logout_day = timezone.localtime(session.logout_at).date()
            if start_day <= logout_day <= end_day and logout_day.weekday() < 5:
                students[session.user_id]["presentDates"].add(logout_day.isoformat())

    eligible_days = sum(1 for offset in range((end_day - start_day).days + 1) if (start_day + timedelta(days=offset)).weekday() < 5)
    rows = []
    for student_id, item in students.items():
        user = item["user"]
        dates = sorted(item["presentDates"])
        present_days = len(dates)
        session_list = item["sessions"]
        sessions_by_day = {}
        for session in session_list:
            sessions_by_day.setdefault(timezone.localtime(session.login_at).date(), []).append(session)
        arrivals = {}
        for session_day, day_sessions in sessions_by_day.items():
            if session_day.weekday() >= 5:
                continue
            first_session = min(day_sessions, key=lambda session: session.login_at)
            arrival_time = timezone.localtime(first_session.login_at).time().replace(tzinfo=None)
            arrivals[session_day.isoformat()] = ("late" if scheduled_start and arrival_time > scheduled_start else "on_time") if scheduled_start else "not_configured"
        latest_session = max(session_list, key=lambda session: session.login_at) if session_list else None
        first_session = min(session_list, key=lambda session: session.login_at) if session_list else None
        latest_logout = max((session.logout_at for session in session_list if session.logout_at), default=None)
        latest_is_open = bool(latest_session and latest_session.logout_at is None and (timezone.now() - latest_session.last_heartbeat_at).total_seconds() <= 180)
        if latest_session and latest_session.logout_at is None and not latest_is_open:
            latest_logout = latest_session.last_heartbeat_at
        duration_seconds = 0
        for session in session_list:
            tracked_seconds = session.active_seconds + session.idle_seconds
            if tracked_seconds:
                duration_seconds += tracked_seconds
            else:
                session_end = session.logout_at or session.last_heartbeat_at
                duration_seconds += max(0, min(int((session_end - session.login_at).total_seconds()), 86400))
        if latest_is_open:
            duration_seconds += max(0, min(int((timezone.now() - latest_session.last_heartbeat_at).total_seconds()), 45))
        late_days = sum(value == "late" for value in arrivals.values())
        on_time_days = sum(value == "on_time" for value in arrivals.values())
        latest_arrival = arrivals.get(max(arrivals), "not_configured") if arrivals else "not_configured"
        rows.append({
            "studentId": student_id,
            "studentName": user.get_full_name() or user.username,
            "studentNumber": user.student_number,
            "presentDays": present_days,
            "eligibleDays": eligible_days,
            "attendancePercent": round(present_days * 100 / eligible_days, 1) if eligible_days else 0,
            "absentDays": max(0, eligible_days - present_days),
            "lastPresent": dates[-1] if dates else "",
            "firstLogin": first_session.login_at.isoformat() if first_session else "",
            "lastLogin": latest_session.login_at.isoformat() if latest_session else "",
            "lastLogout": latest_logout.isoformat() if latest_logout else "",
            "openSession": latest_is_open,
            "logoutEstimated": bool(latest_session and (latest_session.logout_estimated or (latest_session.logout_at is None and not latest_is_open))),
            "durationSeconds": duration_seconds,
            "lateDays": late_days,
            "onTimeDays": on_time_days,
            "arrivalStatus": latest_arrival,
            "dailyAttendance": daily_attendance(session_list, start_day, end_day, scheduled_start),
        })
    rows.sort(key=lambda row: row["studentName"].casefold())
    return JsonResponse({
        "students": rows,
        "eligibleDays": eligible_days,
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "scope": str(class_query.first()) if selected_class_id else "Assigned classes",
        "attendanceBasis": "Portal login days; Monday–Friday are counted as eligible days.",
        "scheduledStartTime": scheduled_start.strftime("%H:%M") if scheduled_start else "",
        "scheduleLevel": "class",
    })


@login_required(login_url="/portal/")
@require_POST
def teacher_attendance_schedule(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return JsonResponse({"error": "Only teachers can manage attendance start times."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Invalid schedule payload")
        class_id = int(payload.get("classId"))
        start_value = str(payload.get("startTime", "")).strip()
        start_time = time.fromisoformat(start_value) if start_value else None
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return JsonResponse({"error": "Choose a valid class and start time."}, status=400)
    can_view_all = request.user.can_view_all_compiler_activity
    class_query = ClassSection.objects.filter(pk=class_id)
    if not can_view_all:
        class_query = class_query.filter(teacher_assignments__teacher=request.user)
    class_section = class_query.first()
    if not class_section:
        return JsonResponse({"error": "Class is not assigned to you."}, status=403)
    class_section.attendance_start_time = start_time
    class_section.save(update_fields=("attendance_start_time",))
    return JsonResponse({"ok": True, "startTime": start_time.strftime("%H:%M") if start_time else ""})


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def teacher_presence_timeout(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return JsonResponse({"error": "Only teachers can change this setting."}, status=403)
    preference, _ = TeacherActivityPreference.objects.get_or_create(teacher=request.user)
    if request.method == "POST":
        try:
            payload = json.loads(request.body or b"{}")
            seconds = int(payload.get("seconds"))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            return JsonResponse({"error": "Choose a valid offline timeout."}, status=400)
        if seconds not in {30, 60, 90, 120, 180, 300, 600}:
            return JsonResponse({"error": "Choose one of the available timeout values."}, status=400)
        preference.presence_offline_timeout_seconds = seconds
        preference.save(update_fields=("presence_offline_timeout_seconds", "updated_at"))
    return JsonResponse({"seconds": preference.presence_offline_timeout_seconds})


def teacher_compiler_activity(request):
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return JsonResponse({"error": "Only teachers can view student activity."}, status=403)
    can_view_all_activity = request.user.can_view_all_compiler_activity
    class_ids = list(ClassSection.objects.values_list("id", flat=True)) if can_view_all_activity else list(TeacherAssignment.objects.filter(teacher=request.user).values_list("class_section_id", flat=True))
    if not class_ids and not can_view_all_activity:
        return JsonResponse({"classes": [], "students": [], "summary": {}, "updatedAt": timezone.now().isoformat()})

    selected_class = request.GET.get("class", "")
    try:
        selected_class_id = int(selected_class) if selected_class else None
    except ValueError:
        return JsonResponse({"error": "Invalid class filter."}, status=400)
    if selected_class_id and selected_class_id not in class_ids:
        return JsonResponse({"error": "Class is not assigned to you."}, status=403)
    assigned_classes = ClassSection.objects.filter(pk__in=class_ids).select_related("course").order_by("course__name", "name")

    day_value = request.GET.get("date", "")
    try:
        report_day = date.fromisoformat(day_value) if day_value else timezone.localdate()
    except ValueError:
        return JsonResponse({"error": "Choose a valid date."}, status=400)

    enrollment_query = Enrollment.objects.filter(class_section_id__in=class_ids, active=True, student__role=PortalUser.Role.STUDENT)
    if selected_class_id:
        enrollment_query = enrollment_query.filter(class_section_id=selected_class_id)
    enrollments = list(enrollment_query.select_related("student", "class_section", "class_section__course").order_by("student__first_name", "student__last_name", "student__username"))
    students_by_id = {}
    for enrollment in enrollments:
        students_by_id.setdefault(enrollment.student_id, {"user": enrollment.student, "classes": []})["classes"].append(enrollment.class_section)
    if can_view_all_activity and not selected_class_id:
        for student in PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username"):
            students_by_id.setdefault(student.pk, {"user": student, "classes": []})
    student_ids = list(students_by_id)

    now = timezone.now()
    presence_setting = TeacherActivityPreference.objects.filter(teacher=request.user).only("presence_offline_timeout_seconds").first()
    offline_timeout_seconds = max(30, min(600, int(presence_setting.presence_offline_timeout_seconds if presence_setting else 90)))
    stale_before = now - timedelta(seconds=offline_timeout_seconds)

    attendance_rows = list(AttendanceSession.objects.filter(user_id__in=student_ids, login_at__date=report_day).order_by("login_at"))
    attendance_by_student = {}
    for session in attendance_rows:
        attendance_by_student.setdefault(session.user_id, []).append(session)

    # Presence is live across calendar boundaries: an open session from yesterday can still be active today.
    current_sessions_query = AttendanceSession.objects.filter(user_id__in=student_ids, logout_at__isnull=True, last_heartbeat_at__gte=stale_before)
    current_slices_query = CompilerActivitySlice.objects.filter(attendance_session__user_id__in=student_ids, ended_at__isnull=True, last_heartbeat_at__gte=stale_before).select_related("attendance_session")
    if selected_class_id:
        single_class_students = Enrollment.objects.filter(active=True).values("student_id").annotate(class_count=Count("class_section_id")).filter(class_count=1).values_list("student_id", flat=True)
        current_sessions_query = current_sessions_query.filter(Q(class_section_id=selected_class_id) | Q(class_section__isnull=True, user_id__in=single_class_students))
        current_slices_query = current_slices_query.filter(Q(class_section_id=selected_class_id) | Q(class_section__isnull=True, attendance_session__user_id__in=single_class_students))
    current_login_by_student = {}
    for session in current_sessions_query.order_by("user_id", "-last_heartbeat_at", "-id"):
        current_login_by_student.setdefault(session.user_id, session)
    current_slice_by_student = {}
    for activity_slice in current_slices_query.order_by("attendance_session__user_id", "-last_heartbeat_at", "-id"):
        current_slice_by_student.setdefault(activity_slice.attendance_session.user_id, activity_slice)
    activity_slices = CompilerActivitySlice.objects.filter(attendance_session__user_id__in=student_ids, started_at__date=report_day).select_related("attendance_session")
    if selected_class_id:
        single_class_students = Enrollment.objects.filter(active=True).values("student_id").annotate(class_count=Count("class_section_id")).filter(class_count=1).values_list("student_id", flat=True)
        activity_slices = activity_slices.filter(Q(class_section_id=selected_class_id) | Q(class_section__isnull=True, attendance_session__user_id__in=single_class_students))
    slice_by_student = {}
    for activity_slice in activity_slices.order_by("started_at", "id"):
        current = slice_by_student.setdefault(activity_slice.attendance_session.user_id, {"slices": [], "active": 0, "idle": 0})
        current["slices"].append(activity_slice)
        current["active"] += activity_slice.active_seconds
        current["idle"] += activity_slice.idle_seconds

    events = CompilerExecutionEvent.objects.filter(student_id__in=student_ids, executed_at__date=report_day)
    paste_events = CompilerPasteEvent.objects.filter(student_id__in=student_ids, created_at__date=report_day)
    client_activity_events = CompilerClientActivityEvent.objects.filter(student_id__in=student_ids, created_at__date=report_day)
    if selected_class_id:
        single_class_students = Enrollment.objects.filter(active=True).values("student_id").annotate(class_count=Count("class_section_id")).filter(class_count=1).values_list("student_id", flat=True)
        events = events.filter(Q(class_section_id=selected_class_id) | Q(class_section__isnull=True, student_id__in=single_class_students))
        paste_events = paste_events.filter(attendance_session__class_section_id=selected_class_id)
        client_activity_events = client_activity_events.filter(attendance_session__class_section_id=selected_class_id)
    event_stats = {row["student_id"]: row for row in events.values("student_id").annotate(
        total=Count("id"), succeeded=Count("id", filter=Q(status="success")), failed=Count("id", filter=Q(status="error")),
        last_execution=Max("executed_at"), total_duration=Sum("duration_ms"),
        typed_chars_total=Sum("typed_chars"), pasted_chars_total=Sum("pasted_chars"), paste_count_total=Sum("paste_count"), edits_after_paste_total=Sum("edits_after_paste"),
        # Count the underlying non-null field directly. The annotation above
        # also exposes a `typed_chars` alias, so filtering on that name here
        # would make Django generate COUNT(... FILTER (WHERE SUM(...) IS NOT
        # NULL)), which SQLite rejects as a nested aggregate.
        tracked_executions=Count("typed_chars"),
    )}
    history_recall_stats = {
        row["student_id"]: row["count"]
        for row in CompilerQueryHistory.objects.filter(
            student_id__in=student_ids,
            executed_at__date=report_day,
            input_method="history_recall",
            is_active=True,
        ).values("student_id").annotate(count=Count("id"))
    }
    last_execution_sql = {}
    for history_row in CompilerQueryHistory.objects.filter(
        student_id__in=student_ids, executed_at__date=report_day, is_active=True,
    ).order_by("student_id", "-executed_at", "-id").values(
        "student_id", "sql", "input_method", "typed_chars", "pasted_chars", "edits_after_paste",
    ):
        last_execution_sql.setdefault(history_row["student_id"], history_row)
    paste_stats = {row["student_id"]: row["count"] for row in paste_events.values("student_id").annotate(count=Count("id"))}
    client_activity_stats = {row["student_id"]: row for row in client_activity_events.values("student_id").annotate(
        page_hidden=Count("id", filter=Q(kind=CompilerClientActivityEvent.Kind.PAGE_HIDDEN)),
        window_blur=Count("id", filter=Q(kind=CompilerClientActivityEvent.Kind.WINDOW_BLUR)),
    )}

    roster = []
    counts = {"active": 0, "idle": 0, "portal": 0, "offline": 0, "present": 0, "success": 0, "failed": 0, "activeSeconds": 0, "idleSeconds": 0, "typedChars": 0, "pastedChars": 0, "pasteCount": 0, "editsAfterPaste": 0, "historyRecallCount": 0, "trackedExecutions": 0, "pasteEvents": 0, "pageHiddenEvents": 0, "windowBlurEvents": 0}
    for student_id, item in students_by_id.items():
        student = item["user"]
        daily_sessions = attendance_by_student.get(student_id, [])
        latest = daily_sessions[-1] if daily_sessions else None
        current_login = current_login_by_student.get(student_id)
        student_activity = slice_by_student.get(student_id, {"slices": [], "active": 0, "idle": 0})
        current_slice = current_slice_by_student.get(student_id)
        state = "offline"
        if current_slice:
            state = "active" if current_slice.compiler_active and current_slice.is_active_now else "idle" if current_slice.current_page == "compiler" else "portal"
        elif current_login:
            state = "portal"
        executions = event_stats.get(student_id, {})
        student_paste_count = paste_stats.get(student_id, 0)
        student_client_events = client_activity_stats.get(student_id, {})
        counts["pasteEvents"] += student_paste_count
        counts["pageHiddenEvents"] += student_client_events.get("page_hidden", 0)
        counts["windowBlurEvents"] += student_client_events.get("window_blur", 0)
        success_count, failed_count = executions.get("succeeded", 0), executions.get("failed", 0)
        if daily_sessions:
            counts["present"] += 1
        counts[state] += 1
        counts["success"] += success_count
        counts["failed"] += failed_count
        counts["activeSeconds"] += student_activity["active"]
        counts["idleSeconds"] += student_activity["idle"]
        counts["typedChars"] += executions.get("typed_chars_total") or 0
        counts["pastedChars"] += executions.get("pasted_chars_total") or 0
        counts["pasteCount"] += executions.get("paste_count_total") or 0
        counts["editsAfterPaste"] += executions.get("edits_after_paste_total") or 0
        history_recall_count = history_recall_stats.get(student_id, 0)
        counts["historyRecallCount"] += history_recall_count
        counts["trackedExecutions"] += executions.get("tracked_executions") or 0
        first_login = daily_sessions[0] if daily_sessions else None
        last_closed = next((session for session in reversed(daily_sessions) if session.logout_at), None)
        status_session = current_slice or current_login or latest
        roster.append({
            "studentId": student_id,
            "studentName": student.get_full_name() or student.username,
            "studentNumber": student.student_number,
            "classNames": [f"{section.course.code} · {section.name} {section.section}".strip() for section in item["classes"]],
            "status": state,
            "attendance": "Present" if first_login else "Absent",
            "inTime": first_login.login_at.isoformat() if first_login else None,
            "outTime": None if current_login else (last_closed.logout_at.isoformat() if last_closed else None),
            "outEstimated": last_closed.logout_estimated if last_closed and not current_login else False,
            "sessionCount": len(daily_sessions),
            "sessionActiveSeconds": student_activity["active"],
            "sessionIdleSeconds": student_activity["idle"],
            "lastSeen": status_session.last_heartbeat_at.isoformat() if status_session else None,
            "lastActivity": status_session.last_activity_at.isoformat() if status_session else None,
            "currentPage": status_session.current_page if status_session else "",
            "compilerActive": bool(current_slice and current_slice.compiler_active),
            "totalExecutions": executions.get("total", 0),
            "successfulExecutions": success_count,
            "failedExecutions": failed_count,
            "lastExecution": executions.get("last_execution").isoformat() if executions.get("last_execution") else None,
            "lastExecutionStatement": last_execution_sql.get(student_id, {}).get("sql", ""),
            "lastExecutionInputMethod": last_execution_sql.get(student_id, {}).get("input_method", ""),
            "lastExecutionTypedChars": last_execution_sql.get(student_id, {}).get("typed_chars"),
            "lastExecutionPastedChars": last_execution_sql.get(student_id, {}).get("pasted_chars"),
            "lastExecutionEditsAfterPaste": last_execution_sql.get(student_id, {}).get("edits_after_paste"),
            "totalExecutionMs": executions.get("total_duration") or 0,
            "typedChars": executions.get("typed_chars_total"),
            "pastedChars": executions.get("pasted_chars_total"),
            "pasteCount": executions.get("paste_count_total"),
            "editsAfterPaste": executions.get("edits_after_paste_total"),
            "historyRecallCount": history_recall_count,
            "trackedExecutions": executions.get("tracked_executions", 0),
            "pasteEvents": student_paste_count,
            "pageHiddenEvents": student_client_events.get("page_hidden", 0),
            "windowBlurEvents": student_client_events.get("window_blur", 0),
        })
    roster.sort(key=lambda row: (row["status"] != "active", row["studentName"].casefold()))
    return JsonResponse({
        "classes": [{"id": section.pk, "name": f"{section.course.code} · {section.name} {section.section}".strip()} for section in assigned_classes],
        "students": roster,
        "summary": {"students": len(roster), **counts},
        "reportDate": report_day.isoformat(),
        "updatedAt": now.isoformat(),
    })


@login_required(login_url="/portal/")
@require_POST
def teacher_approve_registration(request, user_id):
    if not _same_origin(request):
        return JsonResponse({"error": "Approval must be submitted from this portal."}, status=403)
    if _active_portal_role(request) != PortalUser.Role.TEACHER or not request.user.has_portal_role(PortalUser.Role.TEACHER) or not request.user.can_approve_students:
        return JsonResponse({"error": "You do not have permission to approve student registrations."}, status=403)
    student = get_object_or_404(
        PortalUser,
        pk=user_id,
        role=PortalUser.Role.STUDENT,
        registration_status=PortalUser.RegistrationStatus.PENDING_ADMIN,
    )
    student.registration_status = PortalUser.RegistrationStatus.APPROVED
    student.is_active = True
    student.approved_by = request.user
    student.approved_at = timezone.now()
    student.save(update_fields=("registration_status", "is_active", "approved_by", "approved_at"))
    AuditEvent.objects.create(actor=request.user, action="student_registration_approved", target=student.username)
    notification_failures = notify_account_event(student, "approved", request=request)
    message = f"Approved {student.username}."
    if notification_failures:
        message += " Account email or WhatsApp notification could not be sent; check the server log and notification settings."
    return JsonResponse({"ok": True, "message": message})


def _global_default_accent():
    setting = PortalSetting.objects.filter(pk=1).first()
    return setting.default_accent if setting else "violet"


def _dashboard_navigation_items(role):
    setting = PortalSetting.objects.filter(pk=1).first()
    if not setting:
        return []
    return setting.dashboard_navigation_items.filter(is_active=True).filter(
        Q(audience=DashboardNavigationItem.Audience.ALL) | Q(audience=role)
    )
