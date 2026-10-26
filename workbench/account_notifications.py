import logging

from .models import PortalSetting, PortalUser
from .otp_delivery import OTPDeliveryError, send_account_email, send_account_whatsapp


logger = logging.getLogger(__name__)


def notify_account_event(user, event, request=None):
    """Send account notices only through contact channels verified for this user."""
    if event not in {"registered", "approved"}:
        raise ValueError("Unknown account notification event.")

    setting = PortalSetting.objects.filter(pk=1).first()
    if not setting:
        logger.warning("No portal settings found; account notification skipped for user id %s.", user.pk)
        return [channel for channel, verified in (("email", user.email_verified), ("WhatsApp", user.mobile_verified)) if verified]

    name = user.first_name.strip() or user.username
    sign_in_url = request.build_absolute_uri("/portal/") if request else "/portal/"
    if event == "registered" and user.registration_status != PortalUser.RegistrationStatus.APPROVED:
        subject = "Registration received · Online Oracle Compiler"
        body = (
            f"Hello {name},\n\n"
            "We received your account registration. It is awaiting administrator approval.\n"
            f"Your account ID is {user.username}. You can sign in after your account is approved.\n\n"
            "If you did not submit this registration, please contact the portal administrator."
        )
    else:
        subject = "Your Online Oracle Compiler account is ready"
        body = (
            f"Hello {name},\n\n"
            "Your account has been approved and is ready to use.\n"
            f"Your account ID is {user.username}. You can now sign in at {sign_in_url}\n\n"
            "Keep your password private."
        )

    failures = []
    if user.email_verified and user.email:
        try:
            send_account_email(setting, user.email, subject, body)
        except OTPDeliveryError:
            failures.append("email")
            logger.exception("Could not send %s account email for user id %s.", event, user.pk)
    elif user.email_verified:
        failures.append("email")
    if user.mobile_verified and user.mobile_number:
        try:
            send_account_whatsapp(setting, user.mobile_number, body)
        except OTPDeliveryError:
            failures.append("WhatsApp")
            logger.exception("Could not send %s WhatsApp notice for user id %s.", event, user.pk)
    elif user.mobile_verified:
        failures.append("WhatsApp")
    return failures
