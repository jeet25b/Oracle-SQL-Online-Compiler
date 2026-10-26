"""Session-bound recovery through existing verified contacts only."""
import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from django.contrib.auth import password_validation
from django.utils.crypto import salted_hmac
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import PortalUser, PortalSetting, PasswordResetChallenge, AuditEvent, normalize_mobile_number
from .otp_delivery import OTPDeliveryError, send_email_otp, send_whatsapp_otp

ADMIN_MESSAGE = "For a password reset, contact your portal administrator."


def _digest(challenge_id, code):
    return salted_hmac("portal-password-reset", f"{challenge_id}:{code}").hexdigest()


def _channels(user):
    channels = {}
    if user.mobile_verified and user.mobile_number:
        channels["mobile"] = user.mobile_number
    if user.email_verified and user.email:
        channels["email"] = user.email
    return channels


def _mask(channel, value):
    if channel == "mobile":
        return "****" + value[-4:]
    local, domain = value.rsplit("@", 1)
    return local[:1] + "***@" + domain


def _payload(request):
    if len(request.body) > 8192:
        raise ValueError("Request is too large.")
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict):
        raise ValueError("Invalid request.")
    return data


def _limit(request):
    # Bound account lookups/sends per IP without retaining the raw address.
    address = hashlib.sha256(request.META.get("REMOTE_ADDR", "").encode()).hexdigest()
    key = "password-reset-rate:" + address
    if cache.add(key, 1, timeout=600):
        return False
    try:
        return cache.incr(key) > 30
    except ValueError:
        return False


@require_POST
def password_reset_api(request, action):
    if action not in ("options", "send", "complete"):
        return JsonResponse({"error": "Unknown action."}, status=404)
    if _limit(request):
        return JsonResponse({"error": "Too many requests. Please try again later."}, status=429)
    try:
        data = _payload(request)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid reset request."}, status=400)
    if action == "options":
        identifier = data.get("username", "")
        if not isinstance(identifier, str) or not identifier.strip():
            return JsonResponse({"error": "Enter your account ID, email, or mobile number first."}, status=400)
        identifier = identifier.strip()
        query = (Q(username__iexact=identifier) | Q(email__iexact=identifier)
                 | Q(student_number__iexact=identifier) | Q(teacher_number__iexact=identifier))
        mobile = normalize_mobile_number(identifier)
        if mobile:
            query |= Q(mobile_number=mobile)
        users = list(PortalUser.objects.filter(query, is_active=True).distinct()[:2])
        user = users[0] if len(users) == 1 else None
        request.session.pop("password_reset_user", None)
        request.session.pop("password_reset_challenge", None)
        channels = _channels(user) if user else {}
        if not channels:
            return JsonResponse({"channels": [], "message": ADMIN_MESSAGE})
        request.session["password_reset_user"] = user.pk
        request.session["password_reset_started"] = timezone.now().timestamp()
        return JsonResponse({"channels": [{"value": key, "label": ("Verified mobile (WhatsApp)" if key == "mobile" else "Verified email") + " - " + _mask(key, value)} for key, value in channels.items()]})
    user_id = request.session.get("password_reset_user")
    if not user_id or timezone.now().timestamp() - request.session.get("password_reset_started", 0) > 600:
        return JsonResponse({"error": "Start password reset again."}, status=410)
    with transaction.atomic():
        user = PortalUser.objects.select_for_update().filter(pk=user_id, is_active=True).first()
        if not user:
            return JsonResponse({"error": ADMIN_MESSAGE}, status=400)
        channels = _channels(user)
        if action == "send":
            channel = data.get("channel")
            if not isinstance(channel, str) or channel not in channels:
                return JsonResponse({"error": "Choose a verified contact method."}, status=400)
            now = timezone.now()
            recent = PasswordResetChallenge.objects.filter(user=user, created_at__gt=now - timedelta(hours=1))
            if recent.count() >= 5 or recent.filter(created_at__gt=now - timedelta(seconds=60)).exists():
                return JsonResponse({"error": "Please wait before requesting another code."}, status=429)
            setting = PortalSetting.objects.first()
            if not setting:
                return JsonResponse({"error": "Code delivery is not configured. Contact your administrator."}, status=503)
            PasswordResetChallenge.objects.filter(user=user, used=False).update(used=True)
            code = f"{secrets.randbelow(1000000):06d}"
            challenge = PasswordResetChallenge(user=user, channel=channel, destination=channels[channel], password_snapshot=user.password)
            challenge.code_hash = _digest(challenge.pk, code)
            challenge.save()
            try:
                if channel == "email":
                    send_email_otp(setting, channels[channel], code)
                else:
                    send_whatsapp_otp(setting, channels[channel], code)
            except OTPDeliveryError:
                challenge.used = True
                challenge.save(update_fields=["used"])
                return JsonResponse({"error": "The code could not be sent. Try again later or contact your administrator."}, status=503)
            request.session["password_reset_challenge"] = str(challenge.pk)
            return JsonResponse({"message": "Verification code sent. It expires in 10 minutes."})
        challenge = PasswordResetChallenge.objects.select_for_update().filter(pk=request.session.get("password_reset_challenge"), user=user).first()
        if (not challenge or challenge.used or challenge.attempts >= 5 or challenge.expires_at <= timezone.now()
                or challenge.password_snapshot != user.password or channels.get(challenge.channel) != challenge.destination):
            return JsonResponse({"error": "This reset code is no longer valid. Start password reset again."}, status=410)
        code = data.get("code", "")
        if not isinstance(code, str) or len(code) != 6 or not hmac.compare_digest(challenge.code_hash, _digest(challenge.pk, code)):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            return JsonResponse({"error": "The verification code is incorrect."}, status=400)
        password = data.get("password", "")
        if not isinstance(password, str) or not password or len(password) > 1024 or password != data.get("confirmPassword"):
            return JsonResponse({"error": "Enter matching new passwords."}, status=400)
        try:
            password_validation.validate_password(password, user)
        except ValidationError as error:
            return JsonResponse({"error": " ".join(error.messages)}, status=400)
        user.set_password(password)
        user.save(update_fields=["password"])
        PasswordResetChallenge.objects.filter(user=user, used=False).update(used=True)
        AuditEvent.objects.create(actor=user, action="password_reset", target=user.username)
        request.session.pop("password_reset_user", None)
        request.session.pop("password_reset_challenge", None)
        request.session.pop("password_reset_started", None)
        return JsonResponse({"message": "Password reset successfully. Sign in with your new password."})
