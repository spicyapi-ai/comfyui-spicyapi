import json
import unittest
from pathlib import Path

from spicyapi_nodes.fields import (
    MODEL_DEFAULT,
    OMIT,
    OMIT_NUMBER,
    build_fields,
    category_for,
    node_id_for,
    plan_outputs,
    widget_to_value,
)

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "catalog-snapshot.json"
KINDS = {
    "image",
    "images",
    "video",
    "videos",
    "audio",
    "audios",
    "file",
    "string",
    "combo",
    "int",
    "float",
    "bool",
    "loras",
    "json",
    "int_socket",
    "float_socket",
}


def load_models():
    items = json.loads(SNAPSHOT.read_text())["items"]
    return {item["model"]: item for item in items}


def fields_of(schema):
    return {spec.name: spec for spec in build_fields(schema)}


class SnapshotMappingTest(unittest.TestCase):
    def test_every_media_model_maps(self):
        models = load_models()
        media = [m for m in models.values() if m.get("modality") != "text"]
        self.assertGreater(len(media), 100, "the snapshot should hold the whole catalogue")
        for model in media:
            with self.subTest(model=model["model"]):
                specs = build_fields(model["inputSchema"])
                self.assertTrue(specs)
                for spec in specs:
                    self.assertIn(spec.kind, KINDS)
                    if spec.kind == "combo":
                        self.assertIn(spec.default, spec.options)
                        self.assertEqual(len(spec.options), len(spec.option_values))
                    if spec.kind in ("int", "float") and spec.minimum is not None:
                        self.assertGreaterEqual(spec.default, spec.minimum)
                    if spec.kind in ("int", "float") and spec.maximum is not None:
                        self.assertLessEqual(spec.default, spec.maximum)

    def test_node_ids_are_unique(self):
        ids = [node_id_for(m) for m in load_models()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_order_follows_x_ui_order(self):
        model = load_models()["bytedance/seedance-2.5/reference-to-video"]
        orders = [spec.order for spec in build_fields(model["inputSchema"])]
        self.assertEqual(orders, sorted(orders, reverse=True))


class FieldMappingTest(unittest.TestCase):
    def test_reference_uploads_become_socket_groups(self):
        specs = fields_of(load_models()["bytedance/seedance-2.5/reference-to-video"]["inputSchema"])
        self.assertEqual(specs["reference_image_urls"].kind, "images")
        self.assertEqual(specs["reference_video_urls"].kind, "videos")
        self.assertEqual(specs["reference_audio_urls"].kind, "audios")
        self.assertFalse(specs["reference_image_urls"].required)
        self.assertLessEqual(specs["reference_image_urls"].max_items, 16)

    def test_contiguous_duration_enum_is_a_slider(self):
        duration = fields_of(load_models()["bytedance/seedance-2.5/reference-to-video"]["inputSchema"])[
            "duration_seconds"
        ]
        self.assertEqual(duration.kind, "int")
        self.assertEqual((duration.minimum, duration.maximum, duration.default), (4, 30, 5))

    def test_labelled_enum_shows_the_label_and_sends_the_value(self):
        duration = fields_of(load_models()["bytedance/seedance-2.0-mini/text-to-video"]["inputSchema"])[
            "duration_seconds"
        ]
        self.assertEqual(duration.kind, "combo")
        self.assertIn("Smart", duration.options)
        self.assertEqual(widget_to_value(duration, "Smart"), -1)
        self.assertEqual(widget_to_value(duration, "8"), 8)

    def test_seed_is_always_sent(self):
        seed = fields_of(load_models()["alibaba/z-image-turbo-lora/text-to-image"]["inputSchema"])["seed"]
        self.assertTrue(seed.seed)
        self.assertEqual(widget_to_value(seed, 0), 0)

    def test_lora_lists_get_their_own_socket(self):
        specs = fields_of(load_models()["alibaba/z-image-turbo-lora/text-to-image"]["inputSchema"])
        self.assertEqual(specs["loras"].kind, "loras")

    def test_optional_number_without_default_is_omitted_at_minus_one(self):
        schema = {"properties": {"weight": {"type": "number", "minimum": 0, "maximum": 1}}}
        spec = fields_of(schema)["weight"]
        self.assertEqual(spec.default, OMIT_NUMBER)
        self.assertIs(widget_to_value(spec, -1), OMIT)
        self.assertEqual(widget_to_value(spec, 0.4), 0.4)

    def test_unbounded_optional_number_is_socket_only(self):
        spec = fields_of({"properties": {"shift": {"type": "integer"}}})["shift"]
        self.assertEqual(spec.kind, "int_socket")
        self.assertIs(widget_to_value(spec, None), OMIT)

    def test_switch_without_default_has_three_states(self):
        spec = fields_of({"properties": {"flag": {"type": "boolean"}}})["flag"]
        self.assertEqual(spec.options, [MODEL_DEFAULT, "true", "false"])
        self.assertIs(widget_to_value(spec, MODEL_DEFAULT), OMIT)
        self.assertIs(widget_to_value(spec, "false"), False)

    def test_optional_enum_without_default_can_be_left_out(self):
        spec = fields_of({"properties": {"style": {"type": "string", "enum": ["a", "b"]}}})["style"]
        self.assertEqual(spec.default, MODEL_DEFAULT)
        self.assertIs(widget_to_value(spec, MODEL_DEFAULT), OMIT)
        self.assertEqual(widget_to_value(spec, "b"), "b")

    def test_strings(self):
        schema = {
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "x-ui": {"widget": "textarea"}},
                "title": {"type": "string", "x-ui": {"widget": "text"}},
            },
        }
        specs = fields_of(schema)
        self.assertTrue(specs["prompt"].multiline)
        self.assertIs(widget_to_value(specs["title"], "  "), OMIT)
        self.assertEqual(widget_to_value(specs["prompt"], ""), "")

    def test_json_fields_parse_and_reject_bad_json(self):
        schema = {
            "properties": {
                "dialogue": {
                    "type": "array",
                    "x-ui": {"widget": "object-list"},
                    "items": {"type": "object", "properties": {"text": {"type": "string"}}},
                }
            }
        }
        spec = fields_of(schema)["dialogue"]
        self.assertEqual(spec.kind, "json")
        self.assertIs(widget_to_value(spec, ""), OMIT)
        self.assertEqual(widget_to_value(spec, '[{"text": "hi"}]'), [{"text": "hi"}])
        with self.assertRaises(ValueError):
            widget_to_value(spec, "[{")

    def test_hidden_fields_are_skipped(self):
        self.assertEqual(fields_of({"properties": {"x": {"type": "string", "x-ui": {"widget": "hidden"}}}}), {})


class OutputPlanTest(unittest.TestCase):
    def test_plans(self):
        models = load_models()
        self.assertEqual(plan_outputs(models["xai/grok-stt/speech-to-text"]).primary, "text")
        self.assertEqual(plan_outputs(models["spicyapi/character-swap-v1/video-analyze"]).primary, "previews")
        video = plan_outputs(models["bytedance/seedance-2.5/reference-to-video"])
        self.assertEqual((video.primary, video.last_frame), ("video", True))
        self.assertTrue(plan_outputs(models["spicyapi/background-remover-v1/edit"]).alpha)
        self.assertEqual(plan_outputs(models["suno/v6/text-to-audio"]).primary, "audio")

    def test_category(self):
        models = load_models()
        self.assertEqual(category_for(models["bytedance/seedance-2.5/reference-to-video"]), "SpicyAPI/Video/ByteDance")
        self.assertEqual(category_for(models["spicyapi/background-remover-v1/edit"]), "SpicyAPI/Image/Tools")


if __name__ == "__main__":
    unittest.main()
