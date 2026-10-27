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

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, password_validation, update_session_auth_hash
from django.contrib.auth.hashers import make_password
from django.contrib.auth.decorators import login_required
from django.middleware.csrf import get_token
from django.db.models import Q, Count, Sum, Max
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
    CompilerLab,
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


PROJECT_DIR = Path(settings.BASE_DIR) / "online-oracle-compiler"
PORTAL_ASSETS = {"contact-verification.js", "compiler-activity-client.js", "workspace-core.js", "editor-assist.js", "login-autofill.js", "app.js", "styles.css", "dashboard.css", "dashboard.js", "teacher-activity.js", "teacher-attendance.js", "teacher-history.js", "teacher-remote-code.js", "palette.css", "palette.js", "theme.css", "theme.js", "portal-3d.css", "oracle-sql-ide.css", "oracle-sql-ide-extra.css", "oracle-sql-ide-layout.css", "oracle-sql-ide.js", "compiler-icon-controls.css"}
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
        logout(request)
    response = FileResponse((PROJECT_DIR / "index.html").open("rb"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def portal_asset(request, asset):
    if asset not in PORTAL_ASSETS:
        raise Http404
    asset_path = PROJECT_DIR / asset
    if not asset_path.is_file():
        raise Http404
    content_type = mimetypes.guess_type(asset)[0] or "application/octet-stream"
    response = FileResponse(asset_path.open("rb"), content_type=content_type)
    response["Cache-Control"] = "no-cache"
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
    login(request, user)
    request.session.pop("portal_active_role", None)
    request.session.set_expiry(30 * 24 * 60 * 60 if remember else 0)
    if old_session_key and old_session_key != request.session.session_key:
        from .views import _lock, _oracle_sessions
        with _lock:
            oracle_state = _oracle_sessions.pop(old_session_key, None)
            if oracle_state:
                _oracle_sessions[request.session.session_key] = oracle_state
    attendance = AttendanceSession.objects.create(user=user, source="portal")
    request.session["attendance_session_id"] = attendance.pk
    AuditEvent.objects.create(actor=user, action="login", target=user.username)
    next_page = "/portal/choose-role/" if len(roles) > 1 else _dashboard_path(roles[0])
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
        challenge = PendingRegistration.objects.select_for_update().filter(pk=challenge_id).first()
        if not challenge:
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
        challenge = PendingRegistration.objects.select_for_update().filter(pk=challenge_id).first()
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
        challenge = ProfileUpdateChallenge.objects.select_for_update().filter(
            pk=payload["challengeId"], user=request.user,
        ).first()
        if not challenge:
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
    from .views import _lock, _oracle_sessions
    oracle_state = _oracle_sessions.get(session_key) if session_key else None
    if oracle_state:
        for connection_name in ("sqlplus", "editor"):
            connection = oracle_state.get(connection_name)
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass
        with _lock:
            _oracle_sessions.pop(session_key, None)
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
    return JsonResponse({"ok": True, "next": "/portal/"})


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
    assignments = TeacherAssignment.objects.filter(teacher=request.user).select_related("class_section", "class_section__course").order_by("class_section__course__name")
    class_ids = assignments.values_list("class_section_id", flat=True)
    submissions = Submission.objects.filter(assignment__created_by=request.user).select_related("assignment", "student").order_by("-submitted_at")[:6]
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "classes": assignments,
        "class_count": assignments.count(),
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
    classes = ClassSection.objects.filter(
        teacher_assignments__teacher=request.user, is_active=True,
    ).select_related("course").distinct().order_by("course__name", "name", "section")
    class_ids = classes.values_list("id", flat=True)
    labs = CompilerLab.objects.filter(class_section_id__in=class_ids, is_active=True).select_related("class_section", "class_section__course").order_by("class_section__course__name", "class_section__name", "name")
    students = PortalUser.objects.filter(
        role=PortalUser.Role.STUDENT, is_active=True,
        enrollments__class_section_id__in=class_ids, enrollments__active=True,
    ).distinct().order_by("first_name", "last_name", "username")
    message = ""
    error = ""
    if request.method == "POST":
        title = str(request.POST.get("title", "")).strip()
        body = str(request.POST.get("message", "")).strip()
        audience = request.POST.get("audience", "")
        if not title or len(title) > 200 or not body or len(body) > 10000:
            error = "Enter a title (up to 200 characters) and a message (up to 10,000 characters)."
        else:
            target = {"class_section": None, "lab": None, "student": None}
            if audience == "class":
                target_id = request.POST.get("class_id", "")
                target["class_section"] = classes.filter(pk=int(target_id)).first() if target_id.isdigit() else None
            elif audience == "lab":
                target_id = request.POST.get("lab_id", "")
                target["lab"] = labs.filter(pk=int(target_id)).first() if target_id.isdigit() else None
            elif audience == "student":
                target_id = request.POST.get("student_id", "")
                target["student"] = students.filter(pk=int(target_id)).first() if target_id.isdigit() else None
            else:
                error = "Choose a student, class, or lab audience."
            if not error and not any(target.values()):
                error = "Choose a student, class, or lab that belongs to your teaching scope."
            if not error:
                Announcement.objects.create(created_by=request.user, title=title, message=body, **target)
                message = "Announcement published to the selected audience."
    announcements = Announcement.objects.filter(created_by=request.user).filter(
        Q(class_section_id__in=class_ids) | Q(lab__class_section_id__in=class_ids) | Q(student__in=students)
        | Q(class_section__isnull=True, lab__isnull=True, student__isnull=True)
    ).select_related("class_section", "class_section__course", "lab", "lab__class_section", "student").order_by("-created_at")[:50]
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.TEACHER,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.TEACHER),
        "dashboard_home_path": _dashboard_path(PortalUser.Role.TEACHER),
        "announcement_classes": classes,
        "announcement_labs": labs,
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
    assignments = Assignment.objects.filter(class_section__enrollments__student=request.user, class_section__enrollments__active=True, published=True).select_related("class_section", "class_section__course").distinct().order_by("due_at")[:6]
    announcements = Announcement.objects.filter(published=True).filter(
        Q(class_section__isnull=True, lab__isnull=True, student__isnull=True)
        | Q(student=request.user)
        | Q(class_section__enrollments__student=request.user, class_section__enrollments__active=True)
        | Q(lab__class_section__enrollments__student=request.user, lab__class_section__enrollments__active=True)
    ).distinct().order_by("-created_at")[:4]
    shared_code = SharedCompilerCode.objects.filter(published=True).filter(
        Q(class_section__isnull=True, lab__isnull=True, student__isnull=True)
        | Q(student=request.user)
        | Q(class_section__enrollments__student=request.user, class_section__enrollments__active=True)
        | Q(lab__class_section__enrollments__student=request.user, lab__class_section__enrollments__active=True)
        | Q(all_students=True)
    ).select_related("created_by", "class_section", "class_section__course", "lab", "lab__class_section").distinct().order_by("-created_at")[:20]
    context = {
        "user": request.user,
        "portal_role": PortalUser.Role.STUDENT,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(),
        "dashboard_navigation_items": _dashboard_navigation_items(PortalUser.Role.STUDENT),
        "enrollments": enrollments,
        "course_count": enrollments.values("class_section__course_id").distinct().count(),
        "assignment_count": assignments.count(),
        "test_count": SQLTest.objects.filter(published=True, course__classes__enrollments__student=request.user, course__classes__enrollments__active=True).distinct().count(),
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
        teaching_classes = ClassSection.objects.filter(teacher_assignments__teacher=request.user, is_active=True).select_related("course").distinct().order_by("course__name", "name")
        if request.method == "POST":
            if request.POST.get("action") == "setup_class":
                code = request.POST.get("course_code", "").strip().upper()[:32]
                course_name = request.POST.get("course_name", "").strip()[:160]
                class_name = request.POST.get("new_class_name", "").strip()[:80]
                section = request.POST.get("new_section", "").strip()[:32]
                academic_year = request.POST.get("academic_year", "").strip()[:20]
                if not code or not course_name or not class_name:
                    message = "Enter a course code, course name, and class name."
                    tests = SQLTest.objects.filter(created_by=request.user).select_related("class_section", "lab", "course").prefetch_related("questions", "attempts__student", "attempts__answers__question").order_by("-id")
                    for test in tests:
                        test.schedule_label = _test_schedule_label(test)
                    return _render_dashboard("portal-tests.html", _tests_context(
                        request, role, tests=tests, teaching_classes=teaching_classes,
                        labs=CompilerLab.objects.filter(class_section__in=teaching_classes, is_active=True).select_related("class_section"),
                        students=PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username"),
                        message=message,
                    ), request)
                else:
                    with transaction.atomic():
                        course, _ = Course.objects.get_or_create(code=code, defaults={"name": course_name})
                        class_section, _ = ClassSection.objects.get_or_create(
                            course=course, name=class_name, academic_year=academic_year, section=section,
                        )
                        TeacherAssignment.objects.get_or_create(teacher=request.user, class_section=class_section)
                        student_ids = request.POST.getlist("student_ids")
                        students = PortalUser.objects.filter(pk__in=student_ids, role=PortalUser.Role.STUDENT, is_active=True)
                        for student in students:
                            Enrollment.objects.get_or_create(student=student, class_section=class_section)
                    return redirect("/portal/tests/")
            class_section = teaching_classes.filter(pk=_safe_int(request.POST.get("class_section"), None)).first()
            title = request.POST.get("title", "").strip()[:200]
            questions = [value.strip() for value in request.POST.getlist("question") if value.strip()]
            question_file = request.FILES.get("question_file")
            try:
                if question_file:
                    questions.extend(_questions_from_upload(question_file))
                    if len(questions) > 200:
                        raise ValueError("A test can contain at most 200 questions.")
            except ValueError as error:
                message = str(error)
            lab_id = request.POST.get("lab", "").strip()
            lab_pk = _safe_int(lab_id, None) if lab_id else None
            lab = CompilerLab.objects.filter(pk=lab_pk, class_section=class_section, is_active=True).first() if class_section and lab_id else None
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
            elif not class_section or not title or not questions:
                message = "Choose a class, enter a title, and add at least one question."
            elif lab_id and not lab:
                message = "Choose a lab that belongs to the selected class."
            elif frequency not in {choice for choice, _label in SQLTest.ScheduleFrequency.choices}:
                message = "Choose a valid test schedule."
            elif frequency != SQLTest.ScheduleFrequency.ANYTIME and (not schedule_start or not schedule_end or schedule_start == schedule_end):
                message = "Set different start and end times for the test schedule."
            elif frequency == SQLTest.ScheduleFrequency.WEEKLY and (not weekdays or any(day < 0 or day > 6 for day in weekdays)):
                message = "Choose at least one weekday for a weekly test."
            else:
                test = SQLTest.objects.create(
                    course=class_section.course, class_section=class_section, lab=lab,
                    created_by=request.user, title=title,
                    instructions=request.POST.get("instructions", "").strip(),
                    duration_minutes=max(1, min(_safe_int(request.POST.get("duration"), 60), 600)),
                    total_marks=len(questions),
                    published=request.POST.get("published") == "on",
                    schedule_frequency=frequency,
                    schedule_start_time=schedule_start,
                    schedule_end_time=schedule_end,
                    schedule_weekdays=weekdays if frequency == SQLTest.ScheduleFrequency.WEEKLY else [],
                )
                SQLTestQuestion.objects.bulk_create([SQLTestQuestion(test=test, position=index, prompt=prompt) for index, prompt in enumerate(questions, 1)])
                return redirect("/portal/tests/")
        tests = SQLTest.objects.filter(created_by=request.user).select_related("class_section", "lab", "course").prefetch_related("questions", "attempts__student", "attempts__answers__question").order_by("-id")
        for test in tests:
            test.schedule_label = _test_schedule_label(test)
        context = _tests_context(request, role, tests=tests, teaching_classes=teaching_classes, labs=CompilerLab.objects.filter(class_section__in=teaching_classes, is_active=True).select_related("class_section"), students=PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username"), message=message)
    else:
        enrollment_classes = Enrollment.objects.filter(student=request.user, active=True).values_list("class_section_id", flat=True)
        tests = SQLTest.objects.filter(published=True).filter(
            Q(class_section_id__in=enrollment_classes) | Q(class_section__isnull=True, course__classes__enrollments__student=request.user, course__classes__enrollments__active=True)
        ).distinct().select_related("class_section", "lab", "course").prefetch_related("questions")
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
                    for question in test.questions.all():
                        TestAnswer.objects.update_or_create(
                            attempt=attempt, question=question,
                            defaults={"answer": request.POST.get(f"answer_{question.pk}", "").strip()},
                        )
                    attempt.status = "submitted"
                    attempt.submitted_at = now
                    attempt.save(update_fields=("status", "submitted_at"))
                    return redirect("/portal/tests/")
        attempts = {attempt.test_id: attempt for attempt in TestAttempt.objects.filter(student=request.user, test__in=tests).prefetch_related("answers__question")}
        tests = list(tests.order_by("-id"))
        for test in tests:
            test.student_attempt = attempts.get(test.pk)
            test.schedule_open = _test_schedule_is_open(test)
            test.schedule_label = _test_schedule_label(test)
        context = _tests_context(request, role, tests=tests, message=message)
    return _render_dashboard("portal-tests.html", context, request)


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
            rows = [line.strip() for line in content.splitlines() if line.strip()]
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
            rows = [row for row in rows if row]
        else:
            raise ValueError("Use a .txt, .csv, or .docx file. Put one question on each line or row.")
    except (UnicodeDecodeError, csv.Error, zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, OSError):
        raise ValueError("The question file could not be read. Check its format and try again.") from None
    questions = [re.sub(r"^\s*(?:(?:Q\s*)?\d+\s*[.) :]\s*)", "", row).strip() for row in rows]
    questions = [question for question in questions if question]
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
    assignments = TeacherAssignment.objects.filter(teacher=request.user).select_related("class_section", "class_section__course").order_by("class_section__course__name", "class_section__name")
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
    classes = ClassSection.objects.filter(teacher_assignments__teacher=request.user, is_active=True).select_related("course").distinct().order_by("course__name", "name")
    labs = CompilerLab.objects.filter(class_section__in=classes, is_active=True).select_related("class_section", "class_section__course").order_by("class_section__course__name", "class_section__name", "name")
    remote_students = students.order_by("first_name", "last_name", "username")
    share_students = PortalUser.objects.filter(
        role=PortalUser.Role.STUDENT, is_active=True,
        enrollments__active=True, enrollments__class_section__in=classes,
    ).distinct().order_by("first_name", "last_name", "username")
    share_code_message = ""
    share_code_error = ""
    if request.method == "POST":
        title = str(request.POST.get("code_title", "")).strip()
        code = str(request.POST.get("code", ""))
        editor_kind = request.POST.get("editor_kind", "")
        audience = request.POST.get("code_audience", "")
        target = {"class_section": None, "lab": None, "student": None}
        if audience == "all":
            if not PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).exists():
                share_code_error = "There are no active student accounts to receive this code."
        elif audience == "class":
            target_id = request.POST.get("code_class_id", "")
            target["class_section"] = classes.filter(pk=int(target_id)).first() if target_id.isdigit() else None
        elif audience == "lab":
            target_id = request.POST.get("code_lab_id", "")
            target["lab"] = labs.filter(pk=int(target_id)).first() if target_id.isdigit() else None
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
        Q(class_section__in=classes) | Q(lab__in=labs) | Q(student__in=share_students) | Q(all_students=True)
    ).select_related("class_section", "class_section__course", "lab", "lab__class_section", "student").order_by("-created_at")[:30]
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
        "remote_code_labs": labs,
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

    previous_context = (attendance.class_section_id, attendance.lab_id)
    now = timezone.now()
    page = "compiler" if payload.get("page") == "compiler" else "portal"
    active = payload.get("active") is True and payload.get("visible") is True
    lab_id = payload.get("labId")
    if lab_id not in (None, ""):
        try:
            lab_id = int(lab_id)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Choose a valid lab."}, status=400)
        lab_query = CompilerLab.objects.filter(pk=lab_id, is_active=True).select_related("class_section")
        role = _active_portal_role(request)
        if role == PortalUser.Role.STUDENT:
            lab_query = lab_query.filter(class_section__enrollments__student=request.user, class_section__enrollments__active=True)
        elif role == PortalUser.Role.TEACHER:
            lab_query = lab_query.filter(class_section__teacher_assignments__teacher=request.user)
        else:
            return JsonResponse({"error": "This account cannot select a compiler lab."}, status=403)
        lab = lab_query.first()
        if not lab:
            return JsonResponse({"error": "That lab is not assigned to your account."}, status=403)
        attendance.lab = lab
        attendance.class_section = lab.class_section
    elif "labId" in payload:
        attendance.lab = None
        attendance.class_section = None

    elapsed = max(0, min(int((now - attendance.last_heartbeat_at).total_seconds()), 45))
    if elapsed:
        if active: attendance.active_seconds += elapsed
        else: attendance.idle_seconds += elapsed
    previous_slice = attendance.activity_slices.filter(ended_at__isnull=True).order_by("-started_at", "-id").first()
    context_changed = previous_context != (attendance.class_section_id, attendance.lab_id)
    if previous_slice and context_changed:
        previous_slice.ended_at = previous_slice.last_heartbeat_at
        previous_slice.is_active_now = False
        previous_slice.compiler_active = False
        previous_slice.save(update_fields=("ended_at", "is_active_now", "compiler_active"))
        previous_slice = None
    if not previous_slice:
        current_slice = CompilerActivitySlice.objects.create(
            attendance_session=attendance, class_section=attendance.class_section, lab=attendance.lab,
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
        "lab", "class_section", "active_seconds", "idle_seconds", "last_heartbeat_at",
        "current_page", "is_active_now", "compiler_active", "last_activity_at",
        "logout_at", "logout_estimated", "end_reason",
    ))
    return JsonResponse({"ok": True, "serverTime": now.isoformat()})


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def compiler_labs(request):
    if not _same_origin(request):
        return JsonResponse({"error": "Lab settings must come from this portal."}, status=403)
    role = _active_portal_role(request)
    assigned_class_ids = TeacherAssignment.objects.filter(teacher=request.user).values_list("class_section_id", flat=True)
    if request.method == "GET":
        if role == PortalUser.Role.STUDENT:
            labs = CompilerLab.objects.filter(is_active=True, class_section__enrollments__student=request.user, class_section__enrollments__active=True)
        elif role == PortalUser.Role.TEACHER:
            can_view_all_activity = request.GET.get("activity") == "1" and request.user.can_view_all_compiler_activity
            allowed_class_ids = ClassSection.objects.values_list("id", flat=True) if can_view_all_activity else assigned_class_ids
            labs = CompilerLab.objects.filter(class_section_id__in=allowed_class_ids)
            class_id = request.GET.get("class", "")
            if class_id:
                try:
                    class_id = int(class_id)
                except ValueError:
                    return JsonResponse({"error": "Invalid class filter."}, status=400)
                if not (can_view_all_activity and ClassSection.objects.filter(pk=class_id).exists()) and not TeacherAssignment.objects.filter(teacher=request.user, class_section_id=class_id).exists():
                    return JsonResponse({"error": "Class is not assigned to you."}, status=403)
                labs = labs.filter(class_section_id=class_id)
        else:
            return JsonResponse({"error": "Only students and teachers can access compiler labs."}, status=403)
        labs = labs.select_related("class_section", "class_section__course").distinct().order_by("class_section__name", "name")
        return JsonResponse({"labs": [{
            "id": lab.pk, "name": lab.name, "classId": lab.class_section_id,
            "className": f"{lab.class_section.course.code} · {lab.class_section.name} {lab.class_section.section}".strip(),
            "isActive": lab.is_active,
        } for lab in labs]})

    if role != PortalUser.Role.TEACHER:
        return JsonResponse({"error": "Only teachers can create compiler labs."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Lab details could not be read."}, status=400)
    name = str(payload.get("name", "")).strip()[:120]
    try:
        class_id = int(payload.get("classId"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Choose an assigned class."}, status=400)
    class_section = ClassSection.objects.filter(pk=class_id, teacher_assignments__teacher=request.user).first()
    if not class_section:
        return JsonResponse({"error": "You can create labs only for your assigned classes."}, status=403)
    if not name:
        return JsonResponse({"error": "Enter a lab name."}, status=400)
    lab, created = CompilerLab.objects.get_or_create(class_section=class_section, name=name, defaults={"created_by": request.user})
    if not created and not lab.is_active:
        lab.is_active = True
        lab.save(update_fields=("is_active",))
    return JsonResponse({"ok": True, "id": lab.pk, "name": lab.name}, status=201 if created else 200)


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
    lab_id = request.GET.get("lab", "")
    lab_filter = None
    if lab_id:
        try:
            lab_filter = CompilerLab.objects.filter(pk=int(lab_id), class_section_id__in=class_ids, class_section__enrollments__student=student).first()
        except ValueError:
            return JsonResponse({"error": "Invalid lab filter."}, status=400)
        if not lab_filter:
            return JsonResponse({"error": "Lab is not assigned to this student."}, status=403)
        if class_filter and lab_filter.class_section_id != class_filter.pk:
            return JsonResponse({"error": "Lab does not belong to the selected class."}, status=400)
    sessions = AttendanceSession.objects.filter(user=student, login_at__date=report_day).order_by("login_at")
    activity_slices = CompilerActivitySlice.objects.filter(attendance_session__user=student, started_at__date=report_day).select_related("lab", "class_section")
    events = CompilerExecutionEvent.objects.filter(student=student, executed_at__date=report_day).select_related("lab", "class_section")
    paste_events = CompilerPasteEvent.objects.filter(student=student, created_at__date=report_day).select_related("attendance_session")
    client_events = CompilerClientActivityEvent.objects.filter(student=student, created_at__date=report_day)
    if class_filter:
        sessions = sessions.filter(Q(class_section=class_filter) | Q(class_section__isnull=True))
        activity_slices = activity_slices.filter(class_section=class_filter)
        events = events.filter(Q(class_section=class_filter) | Q(lab__class_section=class_filter))
        paste_events = paste_events.filter(attendance_session__class_section=class_filter)
        client_events = client_events.filter(attendance_session__class_section=class_filter)
    if lab_filter:
        sessions = sessions.filter(Q(lab=lab_filter) | Q(lab__isnull=True))
        activity_slices = activity_slices.filter(lab=lab_filter)
        events = events.filter(lab=lab_filter)
        paste_events = paste_events.filter(attendance_session__lab=lab_filter)
        client_events = client_events.filter(attendance_session__lab=lab_filter)
    timeline = []
    for session in sessions:
        timeline.append({"at": session.login_at.isoformat(), "title": "Login · in-time", "detail": f"{session.source} session started.", "kind": "login"})
        if session.last_activity_at and session.last_activity_at > session.login_at:
            timeline.append({"at": session.last_activity_at.isoformat(), "title": "Last active", "detail": f"{session.active_seconds // 60} min active · {session.idle_seconds // 60} min idle.", "kind": "activity"})
        if session.logout_at:
            timeline.append({"at": session.logout_at.isoformat(), "title": "Out-time", "detail": "Estimated from last heartbeat." if session.logout_estimated else "Confirmed at logout.", "kind": "logout", "estimated": session.logout_estimated})
    for activity_slice in activity_slices:
        context_name = activity_slice.lab.name if activity_slice.lab else "Compiler session"
        timeline.append({"at": activity_slice.last_heartbeat_at.isoformat(), "title": f"Activity · {context_name}", "detail": f"{activity_slice.active_seconds // 60} min active · {activity_slice.idle_seconds // 60} min idle" + (f" · {activity_slice.class_section}" if activity_slice.class_section and not activity_slice.lab else ""), "kind": "activity"})
    for event in events:
        detail = f"{event.duration_ms} ms · {event.row_count} rows"
        if event.typed_chars is not None:
            detail += f" · typed {event.typed_chars} · pasted {event.pasted_chars or 0} · paste actions {event.paste_count or 0} · edits after paste {event.edits_after_paste or 0}"
        timeline.append({"at": event.executed_at.isoformat(), "title": f"{event.source} · {'Successful' if event.status == 'success' else 'Failed'}", "detail": detail + (f" · {event.lab.name}" if event.lab else ""), "kind": "execution"})
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
        selected_lab_id = int(request.GET["lab"]) if request.GET.get("lab") else None
    except (ValueError, TypeError):
        return JsonResponse({"error": "Choose valid attendance dates and filters."}, status=400)
    if start_day > end_day or (end_day - start_day).days > 365:
        return JsonResponse({"error": "Choose a date range of up to 366 days."}, status=400)
    if selected_class_id and selected_class_id not in class_ids:
        return JsonResponse({"error": "Class is not assigned to you."}, status=403)

    class_query = ClassSection.objects.filter(pk__in=class_ids)
    if selected_class_id:
        class_query = class_query.filter(pk=selected_class_id)
    selected_lab = None
    if selected_lab_id:
        selected_lab = CompilerLab.objects.filter(pk=selected_lab_id, class_section_id__in=class_ids).select_related("class_section").first()
        if not selected_lab or (selected_class_id and selected_lab.class_section_id != selected_class_id):
            return JsonResponse({"error": "Lab is not assigned to the selected class."}, status=403)
        class_query = class_query.filter(pk=selected_lab.class_section_id)

    enrollments = Enrollment.objects.filter(
        class_section__in=class_query, active=True, student__role=PortalUser.Role.STUDENT, student__is_active=True,
    ).select_related("student").order_by("student__first_name", "student__last_name", "student__username")
    class_section = class_query.first() if selected_class_id or selected_lab else None
    scheduled_start = (selected_lab.attendance_start_time if selected_lab and selected_lab.attendance_start_time else None) or (class_section.attendance_start_time if class_section else None)
    students = {}
    for enrollment in enrollments:
        students.setdefault(enrollment.student_id, {"user": enrollment.student, "presentDates": set(), "sessions": []})
    if can_view_all and not selected_class_id and not selected_lab:
        for student in PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username"):
            students.setdefault(student.pk, {"user": student, "presentDates": set(), "sessions": []})
    student_ids = list(students)
    sessions = AttendanceSession.objects.filter(user_id__in=student_ids).filter(
        Q(login_at__date__gte=start_day, login_at__date__lte=end_day)
        | Q(logout_at__date__gte=start_day, logout_at__date__lte=end_day)
    )
    if selected_lab:
        sessions = sessions.filter(lab_id=selected_lab.pk)
    # Portal login is the attendance signal for a class. For lab-level reports,
    # attendance is recorded only when the student opened that lab in the compiler.
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
        })
    rows.sort(key=lambda row: row["studentName"].casefold())
    return JsonResponse({
        "students": rows,
        "eligibleDays": eligible_days,
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "scope": selected_lab.name if selected_lab else (str(class_query.first()) if selected_class_id else "Assigned classes"),
        "attendanceBasis": "Portal login days; Monday–Friday are counted as eligible days.",
        "scheduledStartTime": scheduled_start.strftime("%H:%M") if scheduled_start else "",
        "scheduleLevel": "lab" if selected_lab and selected_lab.attendance_start_time else "class",
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
        lab_id = int(payload["labId"]) if payload.get("labId") else None
        start_value = str(payload.get("startTime", "")).strip()
        start_time = time.fromisoformat(start_value) if start_value else None
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return JsonResponse({"error": "Choose a valid class, lab, and start time."}, status=400)
    can_view_all = request.user.can_view_all_compiler_activity
    class_query = ClassSection.objects.filter(pk=class_id)
    if not can_view_all:
        class_query = class_query.filter(teacher_assignments__teacher=request.user)
    class_section = class_query.first()
    if not class_section:
        return JsonResponse({"error": "Class is not assigned to you."}, status=403)
    if lab_id:
        lab_query = CompilerLab.objects.filter(pk=lab_id, class_section=class_section)
        if not can_view_all:
            lab_query = lab_query.filter(class_section__teacher_assignments__teacher=request.user)
        lab = lab_query.first()
        if not lab:
            return JsonResponse({"error": "Lab is not assigned to this class."}, status=403)
        lab.attendance_start_time = start_time
        lab.save(update_fields=("attendance_start_time",))
    else:
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
        return JsonResponse({"classes": [], "labs": [], "students": [], "summary": {}, "updatedAt": timezone.now().isoformat()})

    selected_class = request.GET.get("class", "")
    selected_lab = request.GET.get("lab", "")
    try:
        selected_class_id = int(selected_class) if selected_class else None
        selected_lab_id = int(selected_lab) if selected_lab else None
    except ValueError:
        return JsonResponse({"error": "Invalid class or lab filter."}, status=400)
    if selected_class_id and selected_class_id not in class_ids:
        return JsonResponse({"error": "Class is not assigned to you."}, status=403)
    assigned_classes = ClassSection.objects.filter(pk__in=class_ids).select_related("course").order_by("course__name", "name")
    labs_query = CompilerLab.objects.filter(class_section_id__in=class_ids)
    if selected_class_id:
        labs_query = labs_query.filter(class_section_id=selected_class_id)
    if selected_lab_id:
        lab_filter = labs_query.filter(pk=selected_lab_id).first()
        if not lab_filter:
            return JsonResponse({"error": "Lab is not assigned to your classes."}, status=403)
        selected_class_id = lab_filter.class_section_id

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
    if can_view_all_activity and not selected_class_id and not selected_lab_id:
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
    if selected_lab_id:
        current_sessions_query = current_sessions_query.filter(lab_id=selected_lab_id)
        current_slices_query = current_slices_query.filter(lab_id=selected_lab_id)
    elif selected_class_id:
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
    if selected_lab_id:
        activity_slices = activity_slices.filter(lab_id=selected_lab_id)
    elif selected_class_id:
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
    if selected_lab_id:
        events = events.filter(lab_id=selected_lab_id)
    elif selected_class_id:
        single_class_students = Enrollment.objects.filter(active=True).values("student_id").annotate(class_count=Count("class_section_id")).filter(class_count=1).values_list("student_id", flat=True)
        events = events.filter(Q(class_section_id=selected_class_id) | Q(lab__class_section_id=selected_class_id) | Q(class_section__isnull=True, lab__isnull=True, student_id__in=single_class_students))
        paste_events = paste_events.filter(attendance_session__class_section_id=selected_class_id)
        client_activity_events = client_activity_events.filter(attendance_session__class_section_id=selected_class_id)
    if selected_lab_id:
        paste_events = paste_events.filter(attendance_session__lab_id=selected_lab_id)
        client_activity_events = client_activity_events.filter(attendance_session__lab_id=selected_lab_id)
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
        "labs": [{"id": lab.pk, "name": lab.name, "classId": lab.class_section_id, "className": f"{lab.class_section.course.code} · {lab.class_section.name}"} for lab in labs_query.select_related("class_section", "class_section__course").order_by("name")],
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
