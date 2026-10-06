import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_renderer():
    spec = importlib.util.spec_from_file_location("render_docs", ROOT / "scripts" / "render_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DocsTest(unittest.TestCase):
    def test_model_lists_match_the_bundled_catalogue(self):
        """README.md and MODELS.md are generated; a stale copy would advertise the wrong models."""
        render = load_renderer()
        items = render.load()
        readme = (ROOT / "README.md").read_text()
        expected = render.replace_between(readme, "models", render.readme_block(items))
        self.assertEqual(readme, expected, "run python scripts/render_docs.py")
        self.assertEqual((ROOT / "MODELS.md").read_text(), render.models_md(items), "run python scripts/render_docs.py")


if __name__ == "__main__":
    unittest.main()
