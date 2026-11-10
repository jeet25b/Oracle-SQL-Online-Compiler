from django.test import SimpleTestCase, RequestFactory
from django.http import JsonResponse
from .password_privacy import redact_text, redact_payload, PasswordPrivacyMiddleware, HIDDEN


class PasswordPrivacyTests(SimpleTestCase):
    def test_sql_credentials_are_redacted(self):
        for sql in ('CONNECT student/Secret123@db', 'ALTER USER student IDENTIFIED BY "Secret123";',
                    "CREATE USER student IDENTIFIED BY 'Secret123';", 'password=Secret123'):
            with self.subTest(sql=sql):
                self.assertNotIn('Secret123', redact_text(sql))
        self.assertEqual(redact_text('SELECT 1 FROM dual;'), 'SELECT 1 FROM dual;')

    def test_clipboard_and_bare_password_are_not_returned(self):
        result = redact_payload({'events':[{'kind':'paste','preview':'A-secret-that-has-no-label'}],
                                 'pastedTexts':['another-secret'], 'sql':'Secret123!','password':'secret'})
        self.assertEqual(result['events'][0]['preview'], '')
        self.assertEqual(result['pastedTexts'], [])
        self.assertEqual(result['sql'], HIDDEN)
        self.assertEqual(result['password'], HIDDEN)

    def test_identifiers_and_tokens_are_preserved(self):
        data = {'stage_token':'abc12345def67890', 'studentNumber':'S1710260002', 'id':12}
        self.assertEqual(redact_payload(data), data)

    def test_json_response_is_redacted_before_delivery(self):
        response = PasswordPrivacyMiddleware(lambda request: JsonResponse({
            'sql':'CONNECT user/Secret123', 'pastedTexts':['HiddenSecret']}))(RequestFactory().get('/api/compiler/history'))
        self.assertNotIn(b'Secret123', response.content)
        self.assertNotIn(b'HiddenSecret', response.content)
        self.assertEqual(response['Cache-Control'], 'no-store')
