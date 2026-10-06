import json
import unittest
from pathlib import Path

from spicyapi_nodes.pricing import badge_for, describe_price, format_usd, parse_variant_key, variant_key

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "catalog-snapshot.json"


class VariantKeyTest(unittest.TestCase):
    def test_every_published_variant_round_trips(self):
        """Rebuilding each published variant key from its own values must give the same key.

        This is the rule the JavaScript badge follows too; if it drifts from the platform's, the
        badge silently falls back to "from $x" on every node.
        """
        checked = 0
        for model in json.loads(SNAPSHOT.read_text())["items"]:
            badge = badge_for(model)
            if not badge:
                continue
            schema_props = model["inputSchema"].get("properties", {})
            for key in badge["table"]:
                values = parse_variant_key(badge, key)
                widget_values = {}
                connected = set()
                for name in badge["fields"]:
                    target = badge["presence"].get(name)
                    if target is not None:
                        if values.get(name, badge["omitDefaults"].get(name, "false")) == "true":
                            connected.add(target)
                        continue
                    raw = values.get(name, badge["omitDefaults"].get(name))
                    if raw is None:
                        continue
                    if schema_props.get(name, {}).get("type") == "boolean":
                        raw = raw == "true"
                    widget_values[name] = raw
                with self.subTest(model=model["model"], key=key):
                    self.assertEqual(variant_key(badge, widget_values, connected), key)
                checked += 1
        self.assertGreater(checked, 500)

    def test_single_field_keys_are_bare_values(self):
        badge = {"fields": ["resolution"], "presence": {}, "omitDefaults": {}}
        self.assertEqual(variant_key(badge, {"resolution": "720p"}, set()), "720p")

    def test_values_are_query_escaped_when_named(self):
        badge = {"fields": ["aspect_ratio", "resolution"], "presence": {}, "omitDefaults": {}}
        self.assertEqual(
            variant_key(badge, {"aspect_ratio": "16:9", "resolution": "1k"}, set()),
            "aspect_ratio=16%3A9;resolution=1k",
        )

    def test_missing_values_take_the_schema_default(self):
        badge = {
            "fields": ["generate_audio", "resolution"],
            "presence": {},
            "omitDefaults": {},
            "defaults": {"generate_audio": "true"},
        }
        self.assertEqual(variant_key(badge, {"resolution": "480p"}, set()), "generate_audio=true;resolution=480p")

    def test_presence_follows_connections(self):
        badge = {"fields": ["resolution", "with_video"], "presence": {"with_video": "clips"}, "omitDefaults": {}}
        self.assertEqual(variant_key(badge, {"resolution": "720p"}, set()), "resolution=720p;with_video=false")
        self.assertEqual(variant_key(badge, {"resolution": "720p"}, {"clips"}), "resolution=720p;with_video=true")

    def test_defaults_drop_out_when_listed(self):
        badge = {"fields": ["resolution", "search"], "presence": {}, "omitDefaults": {"search": "false"}}
        # With the default dropped only one part is left, and a lone part is written bare.
        self.assertEqual(variant_key(badge, {"resolution": "480p", "search": False}, set()), "480p")
        self.assertEqual(
            variant_key(badge, {"resolution": "480p", "search": True}, set()), "resolution=480p;search=true"
        )


class FormatTest(unittest.TestCase):
    def test_format_usd(self):
        self.assertEqual(format_usd("0.432"), "$0.432")
        self.assertEqual(format_usd("1.08"), "$1.08")
        self.assertEqual(format_usd("0.0246078"), "$0.0246078")
        self.assertEqual(format_usd("2"), "$2.00")
        self.assertEqual(format_usd("0.10"), "$0.10")

    def test_describe(self):
        self.assertEqual(describe_price({"table": {"": "0.01"}, "from": "0.01", "unit": "image"}), "$0.01 per image.")
        self.assertEqual(
            describe_price({"table": {"a": "0.1", "b": "0.2"}, "from": "0.1", "unit": "second"}),
            "From $0.10 per second.",
        )
        self.assertEqual(
            describe_price({"table": {"": "0.02"}, "from": "0.02", "unit": "image", "approximate": True}),
            "About $0.02 per image.",
        )
        self.assertEqual(describe_price(None), "")


if __name__ == "__main__":
    unittest.main()
