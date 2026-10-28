from io import BytesIO
from unittest.mock import Mock
from wsgiref.util import setup_testing_defaults
from django.test import SimpleTestCase
from oraplay.static_delivery import with_static_files


class StaticDeliveryTests(SimpleTestCase):
    def setUp(self):
        self.fallback = Mock(return_value=[b'dynamic'])
        self.app = with_static_files(self.fallback)

    def request(self, path, **headers):
        environ = {}
        setup_testing_defaults(environ)
        environ.update(PATH_INFO=path, REQUEST_METHOD='GET', **headers)
        captured = {}
        def start(status, fields):
            captured.update(status=status, headers=dict(fields))
        response = self.app(environ, start)
        try:
            captured['body'] = b''.join(response)
        finally:
            if hasattr(response, 'close'):
                response.close()
        return captured

    def test_public_asset_bypasses_django(self):
        response = self.request('/portal/assets/dashboard.js')
        self.assertTrue(response['status'].startswith('200'))
        self.assertIn('max-age=300', response['headers']['Cache-Control'])
        self.fallback.assert_not_called()

    def test_conditional_asset_response(self):
        first = self.request('/portal/assets/dashboard.js')
        response = self.request('/portal/assets/dashboard.js', HTTP_IF_NONE_MATCH=first['headers']['ETag'])
        self.assertTrue(response['status'].startswith('304'))
        self.assertEqual(response['body'], b'')

    def test_private_paths_are_not_static(self):
        for path in ['/portal/student/', '/.env', '/lms.sqlite3', '/server.key', '/sql-files/example.sql', '/portal/assets/templates/teacher-remote-code.html']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)['body'], b'dynamic')
        self.assertEqual(self.fallback.call_count, 6)
