from unittest.mock import patch
from datetime import timedelta
from django.test import TestCase, Client
from django.core.cache import cache
from django.utils import timezone
from .models import PortalUser, PortalSetting, ProfileUpdateChallenge


class ContactVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = PortalUser.objects.create_user(username="verify-user", password="Old-password-281!", email="own@example.com", mobile_number="919876543210")
        PortalSetting.objects.create()
        self.client.force_login(self.user)

    def post(self, **data):
        return self.client.post('/api/portal/profile/contact-verification', data, content_type='application/json', HTTP_SEC_FETCH_SITE='same-origin')

    def send(self, channel):
        with patch('workbench.contact_verification.send_email_otp') as email, patch('workbench.contact_verification.send_whatsapp_otp') as mobile:
            response = self.post(action='send', channel=channel)
            self.assertEqual(response.status_code, 200, response.content)
            return (email if channel == 'email' else mobile).call_args.args[2]

    def test_saved_unverified_email_can_be_verified_without_editing(self):
        code = self.send('email')
        self.assertEqual(self.post(action='complete', channel='email', code=code).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertFalse(self.user.mobile_verified)
        self.assertEqual(self.user.email, 'own@example.com')

    def test_mobile_and_status(self):
        code = self.send('mobile')
        self.assertEqual(self.post(action='complete', channel='mobile', code=code).status_code, 200)
        status = self.client.get('/api/portal/profile/contact-verification').json()
        self.assertTrue(status['mobile']['verified'])
        self.assertFalse(status['email']['verified'])

    def test_incorrect_codes_and_expiry_do_not_verify(self):
        code = self.send('email')
        for _ in range(5):
            self.assertEqual(self.post(action='complete', channel='email', code='wrong').status_code, 400)
        self.assertEqual(self.post(action='complete', channel='email', code=code).status_code, 410)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_changed_contact_not_overwritten_by_old_code(self):
        code = self.send('email')
        PortalUser.objects.filter(pk=self.user.pk).update(email='changed@example.com')
        self.assertEqual(self.post(action='complete', channel='email', code=code).status_code, 410)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'changed@example.com')
        self.assertFalse(self.user.email_verified)

    def test_resend_limited_and_payload_cannot_choose_destination(self):
        code = self.send('email')
        self.assertEqual(self.post(action='send', channel='email', email='other@example.com').status_code, 429)
        self.assertEqual(ProfileUpdateChallenge.objects.get().email, 'own@example.com')

    def test_authentication_csrf_and_missing_contact(self):
        self.assertEqual(Client().get('/api/portal/profile/contact-verification').status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/api/portal/profile/contact-verification', {'action':'send','channel':'email'}, content_type='application/json').status_code, 403)
        PortalUser.objects.filter(pk=self.user.pk).update(email='')
        self.assertEqual(self.post(action='send', channel='email').status_code, 400)
