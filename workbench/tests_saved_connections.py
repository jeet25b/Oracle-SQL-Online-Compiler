from unittest.mock import patch
from django.test import TestCase
from django.http import JsonResponse
from .models import PortalUser, OracleConnectionProfile


class SavedConnectionsTests(TestCase):
    def setUp(self):
        self.user = PortalUser.objects.create_user(username="connection-owner", role="TEACHER")
        self.other = PortalUser.objects.create_user(username="other-connection-owner", role="TEACHER")
        self.client.force_login(self.user)
        self.user.set_oracle_credentials("DBUSER", "secret-password", "host/service")
        self.user.save()

    def test_save_multiple_and_select_without_exposing_passwords(self):
        for name in ("Practice", "Reports"):
            with patch("workbench.views._open_oracle_session", return_value=JsonResponse({"connected": True})):
                response = self.client.post("/api/connect", {"user": "DBUSER", "password": "secret-password", "connectString": "host/service", "saveCredentials": True, "connectionName": name}, content_type="application/json", HTTP_SEC_FETCH_SITE="same-origin")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.oracle_connections.count(), 2)
        response = self.client.get("/api/connect-saved", HTTP_SEC_FETCH_SITE="same-origin")
        self.assertEqual(len(response.json()["connections"]), 2)
        self.assertNotIn("secret-password", response.content.decode())
        self.assertNotIn("password_encrypted", response.content.decode())
        saved = self.user.oracle_connections.first()
        self.assertEqual(saved.get_oracle_password(), "secret-password")
        with patch("workbench.views._open_oracle_session", return_value=JsonResponse({"connected": True})) as opener:
            response = self.client.post("/api/connect-saved", {"connectionId": saved.pk}, content_type="application/json", HTTP_SEC_FETCH_SITE="same-origin")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(opener.call_args.args[1:4], ("DBUSER", "secret-password", "host/service"))

    def test_cannot_select_another_users_connection(self):
        saved = OracleConnectionProfile.objects.create(owner=self.other, name="Private", username="OTHER", connect_string="host/service", password_encrypted=self.user.oracle_password_encrypted)
        response = self.client.post("/api/connect-saved", {"connectionId": saved.pk}, content_type="application/json", HTTP_SEC_FETCH_SITE="same-origin")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/connect-saved", HTTP_SEC_FETCH_SITE="same-origin").json()["connections"], [])
