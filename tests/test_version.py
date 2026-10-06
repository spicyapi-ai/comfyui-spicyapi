import re
import unittest
from pathlib import Path

from spicyapi_nodes import VERSION

ROOT = Path(__file__).resolve().parent.parent


class VersionTest(unittest.TestCase):
    def test_pyproject_and_package_agree(self):
        # The registry reads pyproject; the User-Agent reads the package. They must not drift.
        match = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), VERSION)


if __name__ == "__main__":
    unittest.main()
