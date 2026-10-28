from django.utils import timezone


def send_server_crash_report(recipient, description, process_id, resource_summary=""):
    """Email a concise server outage notice without attaching application logs."""
    from .models import PortalSetting
    from .otp_delivery import send_account_email

    recipient = str(recipient or "").strip()
    if not recipient:
        raise ValueError("Crash-report recipient is not configured.")
    setting = PortalSetting.objects.filter(pk=1).first()
    if not setting:
        raise RuntimeError("Portal email settings are unavailable.")

    detected_at = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S %Z")
    subject = "Oracle SQL Workbench server outage report"
    body = (
        "The Oracle SQL Workbench monitor detected that the server is unavailable.\n\n"
        f"Detected at: {detected_at}\n"
        f"Server process ID: {process_id}\n"
        f"Description: {description}\n\n"
        f"Resource utilization:\n{resource_summary or 'Resource metrics were unavailable.'}\n\n"
        "This report contains a short status summary only; server logs are not attached."
    )
    send_account_email(setting, recipient, subject, body)
