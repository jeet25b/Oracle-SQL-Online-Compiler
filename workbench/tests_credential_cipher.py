from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings
import base64
import hashlib

from .models import _portal_secret_cipher


class CredentialCipherTests(SimpleTestCase):
    @override_settings(SECRET_KEY="old-session-secret", DB_CREDENTIALS_ENCRYPTION_KEY="new-credential-secret")
    def test_new_key_encrypts_and_legacy_key_remains_readable(self):
        legacy_key = base64.urlsafe_b64encode(hashlib.sha256(b"old-session-secret").digest())
        legacy_token = Fernet(legacy_key).encrypt(b"saved-password")
        cipher = _portal_secret_cipher()
        self.assertEqual(cipher.decrypt(legacy_token), b"saved-password")
        new_token = cipher.encrypt(b"new-password")
        self.assertEqual(cipher.decrypt(new_token), b"new-password")
        with self.assertRaises(Exception):
            Fernet(legacy_key).decrypt(new_token)

    @override_settings(SECRET_KEY="old-session-secret", DB_CREDENTIALS_ENCRYPTION_KEY="new-credential-secret")
    def test_dedicated_key_is_used_for_new_credential_values(self):
        cipher = _portal_secret_cipher()
        token = cipher.encrypt(b"saved-password")

        dedicated_key = base64.urlsafe_b64encode(
            hashlib.sha256(b"new-credential-secret").digest()
        )
        self.assertEqual(Fernet(dedicated_key).decrypt(token), b"saved-password")
        with self.assertRaises(Exception):
            legacy_key = base64.urlsafe_b64encode(
                hashlib.sha256(b"old-session-secret").digest()
            )
            Fernet(legacy_key).decrypt(token)

    @override_settings(SECRET_KEY="old-session-secret", DB_CREDENTIALS_ENCRYPTION_KEY="new-credential-secret,old-dedicated-secret")
    def test_rotation_key_list_accepts_old_dedicated_ciphertext(self):
        old_key = base64.urlsafe_b64encode(
            hashlib.sha256(b"old-dedicated-secret").digest()
        )
        old_token = Fernet(old_key).encrypt(b"before-rotation")

        cipher = _portal_secret_cipher()
        self.assertEqual(cipher.decrypt(old_token), b"before-rotation")

        new_token = cipher.encrypt(b"after-rotation")
        new_key = base64.urlsafe_b64encode(
            hashlib.sha256(b"new-credential-secret").digest()
        )
        self.assertEqual(Fernet(new_key).decrypt(new_token), b"after-rotation")
