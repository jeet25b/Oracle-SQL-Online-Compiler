import re
import threading

from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend


class OTPDeliveryError(Exception):
    pass


_WHATSAPP_SEND_LOCK = threading.Lock()


def send_email_otp(setting, recipient, code):
    app_password = setting.get_gmail_app_password()
    login_email = setting.gmail_login_email.strip()
    sender = setting.gmail_sender_email.strip()
    if not login_email or not sender or not app_password:
        raise OTPDeliveryError("Email verification is enabled, but Gmail delivery is not configured in Admin.")
    connection = EmailBackend(
        host="smtp.gmail.com", port=587, username=login_email,
        password=app_password, use_tls=True, timeout=15,
    )
    message = EmailMessage(
        subject="Your Online Oracle Compiler verification code",
        body=f"Your verification code is {code}. It expires in 10 minutes. If you did not request this code, ignore this email.",
        from_email=sender,
        to=[recipient],
        connection=connection,
    )
    try:
        message.send(fail_silently=False)
    except Exception as error:
        raise OTPDeliveryError("Gmail could not send the verification email. Check the sender and app password in Admin.") from error


def send_account_email(setting, recipient, subject, body):
    app_password = setting.get_gmail_app_password()
    login_email = setting.gmail_login_email.strip()
    sender = setting.gmail_sender_email.strip()
    if not login_email or not sender or not app_password:
        raise OTPDeliveryError("Gmail account confirmation could not be sent because email delivery is not configured in Admin.")
    connection = EmailBackend(
        host="smtp.gmail.com", port=587, username=login_email,
        password=app_password, use_tls=True, timeout=15,
    )
    message = EmailMessage(subject=subject, body=body, from_email=sender, to=[recipient], connection=connection)
    try:
        message.send(fail_silently=False)
    except Exception as error:
        raise OTPDeliveryError("Gmail could not send the account confirmation. Check the Gmail settings in Admin.") from error


def whatsapp_recipient(setting, normalized_mobile):
    digits = re.sub(r"\D", "", normalized_mobile)
    country_code = re.sub(r"\D", "", setting.whatsapp_default_country_code or "91")
    if len(digits) == 10 and country_code:
        digits = country_code + digits
    if not 7 <= len(digits) <= 15:
        raise OTPDeliveryError("The mobile number must include a valid country code for WhatsApp verification.")
    return digits


def send_whatsapp_otp(setting, mobile, code):
    recipient = "+" + whatsapp_recipient(setting, mobile)
    message = f"Your Online Oracle Compiler verification code is {code}. It expires in 10 minutes. If you did not request this code, ignore this message."
    try:
        import pywhatkit
    except ImportError as error:
        raise OTPDeliveryError("WhatsApp OTP sending requires PyWhatKit. Install the project requirements and restart the server.") from error

    if not _WHATSAPP_SEND_LOCK.acquire(blocking=False):
        raise OTPDeliveryError("Another WhatsApp OTP is being sent. Please wait a moment and submit again.")
    try:
        pywhatkit.sendwhatmsg_instantly(
            recipient,
            message,
            # Allow the conversation to load, then send and close the tab soon after.
            wait_time=12,
            tab_close=True,
            close_time=2,
        )
    except Exception as error:
        raise OTPDeliveryError("WhatsApp Web could not send the code. Sign in to WhatsApp Web in the server user's default browser, keep the Windows session unlocked, and try again.") from error
    finally:
        _WHATSAPP_SEND_LOCK.release()


def send_account_whatsapp(setting, mobile, message):
    recipient = "+" + whatsapp_recipient(setting, mobile)
    try:
        import pywhatkit
    except ImportError as error:
        raise OTPDeliveryError("WhatsApp account confirmation requires PyWhatKit. Install the project requirements and restart the server.") from error

    if not _WHATSAPP_SEND_LOCK.acquire(blocking=False):
        raise OTPDeliveryError("Another WhatsApp message is being sent. Please try again shortly.")
    try:
        pywhatkit.sendwhatmsg_instantly(recipient, message, wait_time=20, tab_close=False)
    except Exception as error:
        raise OTPDeliveryError("WhatsApp Web could not send the account confirmation. Sign in to WhatsApp Web in the server user's default browser and try again.") from error
    finally:
        _WHATSAPP_SEND_LOCK.release()
