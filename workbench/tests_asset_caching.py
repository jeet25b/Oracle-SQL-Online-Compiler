from django.test import SimpleTestCase, RequestFactory
from .portal_views import portal_asset


class AssetCachingTests(SimpleTestCase):
    def test_versioned_asset_can_be_reused_by_browser(self):
        response = portal_asset(RequestFactory().get('/portal/assets/dashboard.js?v=test'), 'dashboard.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'public, max-age=300')
        response.close()

    def test_unchanged_asset_returns_304_without_body(self):
        factory = RequestFactory()
        first = portal_asset(factory.get('/portal/assets/dashboard.js'), 'dashboard.js')
        tag = first['ETag']
        first.close()
        response = portal_asset(factory.get('/portal/assets/dashboard.js', HTTP_IF_NONE_MATCH=tag), 'dashboard.js')
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.content, b'')
        self.assertEqual(response['Cache-Control'], 'no-cache')

    def test_changed_validator_returns_current_file(self):
        response = portal_asset(RequestFactory().get('/portal/assets/dashboard.js', HTTP_IF_NONE_MATCH='"old"'), 'dashboard.js')
        self.assertEqual(response.status_code, 200)
        response.close()
