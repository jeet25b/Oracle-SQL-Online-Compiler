import hmac
import json
import secrets
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import PortalUser, PortalSetting, ProfileUpdateChallenge, AuditEvent
from .otp_delivery import send_email_otp, send_whatsapp_otp, OTPDeliveryError
from .portal_views import _otp_digest, _same_origin


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def contact_verification_api(request):
    def status(user):
        return {"email": {"value": user.email, "verified": user.email_verified},
                "mobile": {"value": user.mobile_number, "verified": user.mobile_verified}}
    if request.method == "GET":
        return JsonResponse(status(request.user))
    if not _same_origin(request):
        return JsonResponse({"error": "Submit verification from this portal."}, status=403)
    try:
        if len(request.body) > 4096:
            raise ValueError()
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid verification request."}, status=400)
    channel = data.get("channel")
    if channel not in ("email", "mobile"):
        return JsonResponse({"error": "Choose email or mobile verification."}, status=400)
    with transaction.atomic():
        user = PortalUser.objects.select_for_update().get(pk=request.user.pk)
        contact = status(user)[channel]
        if contact["verified"]:
            return JsonResponse({"verified": True, "contacts": status(user)})
        if not contact["value"]:
            return JsonResponse({"error": "Add and save this contact in Update profile first."}, status=400)
        if data.get("action") == "send":
            setting = PortalSetting.objects.first()
            if not setting:
                return JsonResponse({"error": "Code delivery is not configured. Contact your administrator."}, status=503)
            rate_key = f"contact-verification:{user.pk}"
            if not cache.add(rate_key, True, 60):
                return JsonResponse({"error": "Wait one minute before requesting another code."}, status=429)
            hourly = f"contact-verification-hour:{user.pk}:{timezone.now().strftime('%Y%m%d%H')}"
            cache.add(hourly, 0, 3600)
            if cache.incr(hourly) > 5:
                return JsonResponse({"error": "Too many codes requested. Try again later."}, status=429)
            code = f"{secrets.randbelow(1000000):06d}"
            challenge = ProfileUpdateChallenge(user=user, email=user.email if channel == "email" else "", mobile_number=user.mobile_number if channel == "mobile" else "")
            field = "email_code_hash" if channel == "email" else "mobile_code_hash"
            setattr(challenge, field, _otp_digest(challenge.pk, code))
            challenge.save()
            try:
                (send_email_otp if channel == "email" else send_whatsapp_otp)(setting, contact["value"], code)
            except OTPDeliveryError:
                challenge.delete()
                return JsonResponse({"error": "Code delivery failed. Try later or contact your administrator."}, status=503)
            request.session[f"verify_contact_{channel}"] = str(challenge.pk)
            return JsonResponse({"message": "Code sent. It expires in 10 minutes."})
        if data.get("action") != "complete":
            return JsonResponse({"error": "Unknown verification action."}, status=400)
        challenge = ProfileUpdateChallenge.objects.select_for_update().filter(pk=request.session.get(f"verify_contact_{channel}"), user=user).first()
        destination = getattr(challenge, "email" if channel == "email" else "mobile_number", None)
        if not challenge or challenge.attempts >= 5 or challenge.expires_at <= timezone.now() or destination != contact["value"]:
            return JsonResponse({"error": "This code expired or the contact changed. Request a new code."}, status=410)
        code = data.get("code", "")
        digest = challenge.email_code_hash if channel == "email" else challenge.mobile_code_hash
        if not isinstance(code, str) or len(code) != 6 or not hmac.compare_digest(digest, _otp_digest(challenge.pk, code)):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            return JsonResponse({"error": "Incorrect verification code."}, status=400)
        # Verify ownership without changing the saved contact or other profile fields.
        if PortalUser.objects.filter(**{"email__iexact" if channel == "email" else "mobile_number": contact["value"]}).exclude(pk=user.pk).exists():
            return JsonResponse({"error": "Another account uses this contact. Update your profile first."}, status=409)
        verified_field = "email_verified" if channel == "email" else "mobile_verified"
        setattr(user, verified_field, True)
        user.save(update_fields=[verified_field])
        challenge.delete()
        request.session.pop(f"verify_contact_{channel}", None)
        AuditEvent.objects.create(actor=user, action="profile_contact_verified", target=user.username)
        return JsonResponse({"verified": True, "contacts": status(user)})
