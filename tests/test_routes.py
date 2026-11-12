import os
import tempfile
import unittest
from unittest import mock

from aiohttp.test_utils import make_mocked_request

from spicyapi_nodes import config, routes


class SettingsGuardTest(unittest.TestCase):
    """Settings changes must come from the panel, and the lock must turn them off entirely."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {config.ENV_CONFIG_DIR: self.home.name}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.home.cleanup)
        os.environ.pop(config.ENV_LOCK_SETTINGS, None)

    def request(self, headers):
        return make_mocked_request("POST", "/spicyapi/settings", headers=headers)

    def test_panel_request_passes(self):
        req = self.request({routes.SETTINGS_HEADER: "1", "Content-Type": "application/json"})
        self.assertIsNone(routes._refuse_change(req, needs_json=True))

    def test_missing_header_is_refused(self):
        req = self.request({"Content-Type": "application/json"})
        self.assertEqual(routes._refuse_change(req, needs_json=True).status, 403)

    def test_simple_request_body_is_refused(self):
        # A cross-site form can send text/plain without a preflight; it must not be read as JSON.
        req = self.request({routes.SETTINGS_HEADER: "1", "Content-Type": "text/plain"})
        self.assertEqual(routes._refuse_change(req, needs_json=True).status, 415)

    def test_delete_needs_header_but_not_json(self):
        req = make_mocked_request("DELETE", "/spicyapi/api-key", headers={routes.SETTINGS_HEADER: "1"})
        self.assertIsNone(routes._refuse_change(req, needs_json=False))

    def test_lock_refuses_even_the_panel(self):
        os.environ[config.ENV_LOCK_SETTINGS] = "1"
        req = self.request({routes.SETTINGS_HEADER: "1", "Content-Type": "application/json"})
        self.assertEqual(routes._refuse_change(req, needs_json=True).status, 403)


if __name__ == "__main__":
    unittest.main()
