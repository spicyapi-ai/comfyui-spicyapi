import os
import stat
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

from spicyapi_nodes import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {config.ENV_CONFIG_DIR: self.home.name}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.home.cleanup)
        os.environ.pop(config.ENV_API_KEY, None)

    def test_no_key(self):
        self.assertEqual(config.get_api_key(), ("", "none"))

    def test_saved_key_is_owner_only(self):
        config.save_api_key("  sk-spicy-abcdefgh1234  ")
        self.assertEqual(config.get_api_key(), ("sk-spicy-abcdefgh1234", "file"))
        if os.name == "posix":
            mode = stat.S_IMODE(os.stat(os.path.join(self.home.name, "config.json")).st_mode)
            self.assertEqual(mode, 0o600)

    def test_environment_wins(self):
        config.save_api_key("sk-spicy-from-file-0000")
        with mock.patch.dict(os.environ, {config.ENV_API_KEY: "sk-spicy-from-env-1111"}):
            self.assertEqual(config.get_api_key(), ("sk-spicy-from-env-1111", "env"))

    def test_clear_keeps_other_settings(self):
        config.save_api_key("sk-spicy-abcdefgh1234")
        config.save_max_cost_per_run("2.5")
        config.clear_api_key()
        self.assertEqual(config.get_api_key(), ("", "none"))
        self.assertEqual(config.get_max_cost_per_run(), Decimal("2.5"))

    def test_hint_never_shows_more_than_four_characters(self):
        self.assertEqual(config.key_hint("sk-spicy-abcdefgh1234"), "...1234")
        self.assertEqual(config.key_hint("short"), "...")
        self.assertEqual(config.key_hint(""), "")

    def test_cost_limit(self):
        self.assertIsNone(config.get_max_cost_per_run())
        self.assertIsNone(config.save_max_cost_per_run(0))
        self.assertIsNone(config.get_max_cost_per_run())
        self.assertEqual(config.save_max_cost_per_run("1.25"), Decimal("1.25"))
        with self.assertRaises(ValueError):
            config.save_max_cost_per_run("-1")
        with self.assertRaises(ValueError):
            config.save_max_cost_per_run("lots")

    def test_base_url(self):
        self.assertEqual(config.get_base_url(), "https://api.spicyapi.ai")
        with mock.patch.dict(os.environ, {config.ENV_BASE_URL: "http://127.0.0.1:9000/"}):
            self.assertEqual(config.get_base_url(), "http://127.0.0.1:9000")


if __name__ == "__main__":
    unittest.main()
