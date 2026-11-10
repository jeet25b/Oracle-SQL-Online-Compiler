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

    def test_history_formatter_assets_are_public_and_loadable(self):
        for name, marker in [('history-sql.js', b'HistorySql'), ('history-sql.css', b'history-syntax')]:
            with self.subTest(asset=name):
                response = self.request('/portal/assets/' + name)
                self.assertTrue(response['status'].startswith('200'))
                self.assertIn(marker, response['body'])
        self.fallback.assert_not_called()

    def test_advanced_editor_and_export_assets_are_public(self):
        for name in ('workbench-pro.js', 'workbench-pro.css'):
            with self.subTest(asset=name):
                self.assertTrue(self.request('/portal/assets/' + name)['status'].startswith('200'))
        for name in ('monaco/vs/loader.js', 'monaco/vs/editor/editor.main.js', 'exceljs/exceljs.min.js'):
            with self.subTest(vendor=name):
                self.assertTrue(self.request('/portal/vendor/' + name)['status'].startswith('200'))
        self.fallback.assert_not_called()

    def test_native_compiler_assets_are_public(self):
        for name in ('native-compiler.js', 'native-compiler.css'):
            with self.subTest(asset=name):
                self.assertTrue(self.request('/portal/assets/' + name)['status'].startswith('200'))
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
