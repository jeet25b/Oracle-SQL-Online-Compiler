from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from .models import PortalUser, PortalSetting, PasswordResetChallenge
from .password_reset import ADMIN_MESSAGE


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = PortalUser.objects.create_user(username="recovery-user", password="Old-password-982!",
            email="verified@example.com", mobile_number="919876543210")
        PortalSetting.objects.create()

    def post(self, action, **payload):
        return self.client.post("/api/portal/password-reset/" + action, payload, content_type="application/json")

    def verify_contacts(self, email=True, mobile=False):
        self.user.email_verified = email
        self.user.mobile_verified = mobile
        self.user.save(update_fields=["email_verified", "mobile_verified"])

    def start(self, channel="email"):
        self.post("options", username=self.user.username)
        with patch("workbench.password_reset.send_email_otp") as email, patch("workbench.password_reset.send_whatsapp_otp") as mobile:
            response = self.post("send", channel=channel)
            self.assertEqual(response.status_code, 200, response.content)
            return (email if channel == "email" else mobile).call_args.args[2]

    def finish(self, code, client=None):
        return (client or self.client).post("/api/portal/password-reset/complete", {
            "code": code, "password": "Fresh-password-729!", "confirmPassword": "Fresh-password-729!"
        }, content_type="application/json")

    def test_no_verified_contact_and_unknown_account(self):
        for name in (self.user.username, "missing-user"):
            response = self.post("options", username=name)
            self.assertEqual(response.json(), {"channels": [], "message": ADMIN_MESSAGE})

    def test_only_email_and_only_mobile_and_both(self):
        for email, mobile, expected in [(True, False, ["email"]), (False, True, ["mobile"]), (True, True, ["mobile", "email"])]:
            self.verify_contacts(email, mobile)
            channels = self.post("options", username=self.user.username).json()["channels"]
            self.assertEqual([c["value"] for c in channels], expected)
            self.assertNotIn(self.user.email, str(channels))
            self.assertNotIn(self.user.mobile_number, str(channels))

    def test_unverified_channel_rejected(self):
        self.verify_contacts()
        self.post("options", username=self.user.username)
        self.assertEqual(self.post("send", channel="mobile").status_code, 400)

    def test_email_reset_and_replay_rejected(self):
        self.verify_contacts()
        code = self.start()
        challenge = PasswordResetChallenge.objects.get()
        self.assertNotEqual(challenge.code_hash, code)
        self.assertEqual(self.finish(code).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Fresh-password-729!"))
        self.assertFalse(self.user.check_password("Old-password-982!"))
        self.assertEqual(self.finish(code).status_code, 410)

    def test_mobile_delivery_and_reset(self):
        self.verify_contacts(False, True)
        code = self.start("mobile")
        self.assertEqual(self.finish(code).status_code, 200)

    def test_wrong_code_locked_after_five_and_session_binding(self):
        self.verify_contacts()
        code = self.start()
        self.assertEqual(self.finish(code, Client()).status_code, 410)
        for _ in range(5):
            self.assertEqual(self.finish("not-valid").status_code, 400)
        self.assertEqual(self.finish(code).status_code, 410)

    def test_expired_or_changed_contact_rejected(self):
        self.verify_contacts()
        code = self.start()
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        self.assertEqual(self.finish(code).status_code, 410)
        self.verify_contacts()
        PasswordResetChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.finish(code).status_code, 410)

    def test_resend_throttled_and_weak_password_rejected(self):
        self.verify_contacts()
        code = self.start()
        self.assertEqual(self.post("send", channel="email").status_code, 429)
        response = self.post("complete", code=code, password="123", confirmPassword="123")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.finish(code).status_code, 200)

    def test_delivery_failure_cannot_be_used(self):
        from .otp_delivery import OTPDeliveryError
        self.verify_contacts()
        self.post("options", username=self.user.username)
        with patch("workbench.password_reset.send_email_otp", side_effect=OTPDeliveryError("delivery failed")):
            self.assertEqual(self.post("send", channel="email").status_code, 503)
        self.assertTrue(PasswordResetChallenge.objects.get().used)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/api/portal/password-reset/options", {"username": self.user.username}, content_type="application/json").status_code, 403)
