"""Managed workflow helpers, client endpoints, node execution and flagship example templates."""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from spicyapi_nodes.workflows import (
    WORKFLOW_PRESETS,
    build_workflow_input,
    first_asset_url,
    preset_media_kind,
    workflow_done_line,
    workflow_status_line,
)

try:
    from aiohttp import web
    from aiohttp.test_utils import TestServer
except ImportError:  # pragma: no cover
    web = None

if web is not None:
    from spicyapi_nodes.client import SpicyApiError, SpicyClient, SpicyWorkflowFailed

ROOT = Path(__file__).resolve().parent.parent


def envelope(data, code=200, status=200, **headers):
    body = {"code": code, "msg": "success" if code == 200 else f"failed {code}", "data": data, "request_id": "req_wf"}
    return web.json_response(body, status=status, headers=headers)


class WorkflowInputTest(unittest.TestCase):
    def test_image_to_video_hd_builds_exact_payload(self):
        payload = build_workflow_input(
            "image-to-video-hd",
            prompt="Warm sunset glow",
            media_url="spicy://f/fil_img",
            edit_prompt="Restyle at sunset",
            video_prompt="Slow dolly in",
            aspect_ratio="16:9",
            resolution="1080p",
            duration_seconds=5,
            generate_audio="true",
        )
        self.assertEqual(
            payload,
            {
                "prompt": "Warm sunset glow",
                "image_url": "spicy://f/fil_img",
                "edit_prompt": "Restyle at sunset",
                "video_prompt": "Slow dolly in",
                "aspect_ratio": "16:9",
                "resolution": "1080p",
                "duration_seconds": 5,
                "generate_audio": True,
            },
        )

    def test_image_to_4k_upscale_supports_output_format_override(self):
        payload = build_workflow_input(
            "image-to-4k-upscale",
            prompt="Sharpen studio portrait",
            media_url="https://example.com/a.png",
            resolution="4k",
            extra_options_json='{"output_format": "png"}',
        )
        self.assertEqual(
            payload,
            {
                "prompt": "Sharpen studio portrait",
                "image_url": "https://example.com/a.png",
                "resolution": "4k",
                "output_format": "png",
            },
        )

    def test_video_extend_hd_maps_resolution_to_target_resolution(self):
        self.assertEqual(preset_media_kind("video-extend-hd"), "video")
        payload = build_workflow_input(
            "video-extend-hd",
            prompt="Continue camera pan right",
            media_url="spicy://f/fil_vid",
            resolution="1080p",
            duration_seconds=6,
            generate_audio="false",
            extra_options_json='{"extend_resolution": "720p"}',
        )
        self.assertEqual(
            payload,
            {
                "prompt": "Continue camera pan right",
                "video_url": "spicy://f/fil_vid",
                "target_resolution": "1080p",
                "duration_seconds": 6,
                "generate_audio": False,
                "extend_resolution": "720p",
            },
        )

    def test_rejects_missing_required_inputs_and_invalid_options(self):
        with self.assertRaisesRegex(ValueError, "Unknown workflow preset"):
            build_workflow_input("unknown-preset", prompt="hi", media_url="https://x/a.png")
        with self.assertRaisesRegex(ValueError, "'prompt' is required"):
            build_workflow_input("image-to-video-hd", prompt="   ", media_url="https://x/a.png")
        with self.assertRaisesRegex(ValueError, "requires a source image"):
            build_workflow_input("image-to-video-hd", prompt="Animate", media_url="")
        with self.assertRaisesRegex(ValueError, "requires a source video"):
            build_workflow_input("video-extend-hd", prompt="Extend", media_url="")
        with self.assertRaisesRegex(ValueError, "Unsupported input field"):
            build_workflow_input(
                "image-to-4k-upscale",
                prompt="Enhance",
                media_url="https://x/a.png",
                extra_options_json='{"video_prompt": "nope"}',
            )
        with self.assertRaisesRegex(ValueError, "must be one of: 720p, 1080p"):
            build_workflow_input(
                "image-to-video-hd",
                prompt="Animate",
                media_url="https://x/a.png",
                resolution="4k",
            )
        with self.assertRaisesRegex(ValueError, "between 3 and 15"):
            build_workflow_input(
                "image-to-video-hd",
                prompt="Animate",
                media_url="https://x/a.png",
                duration_seconds=20,
            )

    def test_status_and_asset_helpers(self):
        rec = {
            "workflowId": "wfr_123",
            "state": "running",
            "currentStep": 1,
            "totalSteps": 2,
            "totalCost": "0.145",
            "steps": [
                {"index": 0, "title": "Edit Source Image", "state": "succeeded"},
                {"index": 1, "title": "Generate HD Video", "state": "running"},
            ],
            "output": {
                "assets": [
                    {"url": "https://files.test/pending.mp4", "pending": True},
                    {"url": "https://files.test/final.mp4", "mime": "video/mp4"},
                ]
            },
        }
        self.assertEqual(first_asset_url(rec), "https://files.test/final.mp4")
        self.assertIn("Step 2/2 (Generate HD Video): Running 12s", workflow_status_line(rec, 12.4))
        self.assertIn("Workflow wfr_123", workflow_status_line(rec, 12.4))
        self.assertIn("Total $0.145", workflow_done_line(rec, 34.8))


@unittest.skipIf(web is None, "aiohttp is not installed")
class WorkflowClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for name in ("NO_PROXY", "no_proxy"):
            previous = os.environ.get(name)
            os.environ[name] = "127.0.0.1,localhost"
            self.addCleanup(lambda n=name, v=previous: os.environ.pop(n) if v is None else os.environ.__setitem__(n, v))
        self.calls = {}
        self.run_requests = []
        self.download_url_bodies = []
        app = web.Application()
        app.router.add_get("/api/v1/workflows", self.list_workflows)
        app.router.add_post("/api/v1/workflows/run", self.run_workflow)
        app.router.add_get("/api/v1/workflows/{workflow_id}", self.get_workflow)
        app.router.add_post("/api/v1/common/download-url", self.download_url)
        self.server = TestServer(app)
        await self.server.start_server()
        self.base = str(self.server.make_url("")).rstrip("/")
        self.client = SpicyClient("sk-test", self.base)

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    def count(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        return self.calls[name]

    async def list_workflows(self, request):
        assert request.headers["Authorization"] == "Bearer sk-test"
        return envelope({"items": [{"id": preset} for preset in WORKFLOW_PRESETS]})

    async def run_workflow(self, request):
        body = await request.json()
        self.run_requests.append((request.headers.get("Idempotency-Key"), body))
        if self.count("run") == 1:
            return envelope(None, code=503, status=503, **{"Retry-After": "0"})
        return envelope(
            {
                "workflowId": "wfr_1",
                "preset": body["preset"],
                "state": "queued",
                "currentStep": 0,
                "totalSteps": 2,
                "totalCost": "0.028",
                "steps": [],
            },
            status=202,
        )

    async def get_workflow(self, request):
        wf_id = request.match_info["workflow_id"]
        if wf_id == "wfr_fail":
            return envelope(
                {
                    "workflowId": wf_id,
                    "preset": "image-to-video-hd",
                    "state": "failed",
                    "errorCode": "content_rejected",
                    "errorMessage": "Declined",
                    "steps": [],
                }
            )
        n = self.count(f"get:{wf_id}")
        if n == 1:
            return envelope(
                {
                    "workflowId": wf_id,
                    "preset": "image-to-video-hd",
                    "state": "running",
                    "currentStep": 1,
                    "totalSteps": 2,
                    "totalCost": "0.08",
                    "steps": [],
                }
            )
        if n == 2:
            return envelope(
                {
                    "workflowId": wf_id,
                    "preset": "image-to-video-hd",
                    "state": "succeeded",
                    "currentStep": 1,
                    "totalSteps": 2,
                    "totalCost": "0.12",
                    "output": {"assets": [{"mime": "video/mp4", "pending": True}]},
                    "steps": [],
                }
            )
        return envelope(
            {
                "workflowId": wf_id,
                "preset": "image-to-video-hd",
                "state": "succeeded",
                "currentStep": 1,
                "totalSteps": 2,
                "totalCost": "0.12",
                "output": {"assets": [{"mime": "video/mp4", "url": "https://files.test/out.mp4"}]},
                "steps": [],
            }
        )

    async def download_url(self, request):
        body = await request.json()
        self.download_url_bodies.append(body)
        return envelope({"url": "https://files.test/signed.mp4?download=1"})

    async def test_list_and_run_workflow_retry_keeps_key(self):
        presets = await self.client.list_workflows()
        self.assertEqual([p["id"] for p in presets], list(WORKFLOW_PRESETS))

        created = await self.client.run_workflow(
            "image-to-video-hd",
            {"image_url": "spicy://f/fil_1", "prompt": "Animate"},
            idempotency_key="wf-key-1",
        )
        self.assertEqual(created["workflowId"], "wfr_1")
        self.assertEqual(len(self.run_requests), 2)
        self.assertEqual(self.run_requests[0][0], "wf-key-1")
        self.assertEqual(self.run_requests[1][0], "wf-key-1")
        self.assertEqual(self.run_requests[1][1]["preset"], "image-to-video-hd")

    async def test_wait_for_workflow_waits_for_final_non_pending_asset(self):
        seen_states = []
        with mock.patch("spicyapi_nodes.client.interruptible_sleep", new=mock.AsyncMock()):
            final = await self.client.wait_for_workflow(
                "wfr_1",
                timeout_seconds=60,
                on_poll=lambda rec, _: seen_states.append(rec["state"]),
            )
        self.assertEqual(seen_states, ["running", "succeeded", "succeeded"])
        self.assertEqual(final["output"]["assets"][0]["url"], "https://files.test/out.mp4")

    async def test_workflow_failed_error_formats_message(self):
        with mock.patch("spicyapi_nodes.client.interruptible_sleep", new=mock.AsyncMock()):
            failed = await self.client.wait_for_workflow("wfr_fail", timeout_seconds=30)
        err = SpicyWorkflowFailed(failed)
        self.assertIn("content_rejected: Declined", str(err))
        self.assertIn("workflow wfr_fail", str(err))

    async def test_download_url_supports_download_flag(self):
        res = await self.client.download_url("https://files.test/out.mp4", download=True)
        self.assertEqual(res["url"], "https://files.test/signed.mp4?download=1")
        self.assertEqual(self.download_url_bodies, [{"url": "https://files.test/out.mp4", "download": True}])


class ExampleWorkflowTemplatesTest(unittest.TestCase):
    def test_flagship_templates_are_valid_comfyui_graphs(self):
        templates = {
            "seedream-5.5-to-wan-3.0-video.json": {
                "SpicyAPI_bytedance_seedream_5_5_text_to_image",
                "SpicyAPI_alibaba_wan_3_0_image_to_video",
                "SaveImage",
                "SaveVideo",
            },
            "managed-workflow-image-to-video-hd.json": {
                "LoadImage",
                "SpicyAPIWorkflowRun",
                "SaveVideo",
                "PreviewAny",
            },
        }
        for filename, expected_types in templates.items():
            path = ROOT / "example_workflows" / filename
            self.assertTrue(path.is_file(), f"missing {filename}")
            graph = json.loads(path.read_text(encoding="utf-8"))
            node_types = {node["type"] for node in graph["nodes"]}
            self.assertTrue(expected_types.issubset(node_types), f"{filename} missing nodes")
            node_ids = {node["id"] for node in graph["nodes"]}
            for link in graph["links"]:
                _, src_id, _, dst_id, _, _ = link
                self.assertIn(src_id, node_ids)
                self.assertIn(dst_id, node_ids)


class WorkflowNodeStubTest(unittest.IsolatedAsyncioTestCase):
    """Exercise SpicyAPIWorkflowRun schema and execute path with minimal ComfyUI V3 stubs."""

    def _install_comfy_stubs(self):
        class _Input:
            def __init__(self, kind, name, **kwargs):
                self.kind = kind
                self.name = name
                self.kwargs = kwargs

        class _Output:
            def __init__(self, kind, name, **kwargs):
                self.kind = kind
                self.name = name
                self.kwargs = kwargs

        def _make_type(kind):
            return types.SimpleNamespace(
                Input=lambda name, **kw: _Input(kind, name, **kw),
                Output=lambda name, **kw: _Output(kind, name, **kw),
            )

        class _Schema:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class _ComfyNode:
            hidden = None

            @classmethod
            def GET_SCHEMA(cls):
                return cls.define_schema()

        class _NodeOutput:
            def __init__(self, *values):
                self.values = values

        class _ExecutionBlocker:
            def __init__(self, message):
                self.message = message

        io_stub = types.SimpleNamespace(
            ComfyNode=_ComfyNode,
            Schema=_Schema,
            NodeOutput=_NodeOutput,
            Input=_Input,
            Output=_Output,
            Custom=lambda name: _make_type(name),
            Image=_make_type("IMAGE"),
            Mask=_make_type("MASK"),
            Video=_make_type("VIDEO"),
            Audio=_make_type("AUDIO"),
            String=_make_type("STRING"),
            Int=_make_type("INT"),
            Float=_make_type("FLOAT"),
            Boolean=_make_type("BOOLEAN"),
            Combo=_make_type("COMBO"),
            Autogrow=types.SimpleNamespace(
                Input=lambda name, **kw: _Input("AUTOGROW", name, **kw),
                TemplatePrefix=lambda *a, **kw: (a, kw),
            ),
            ControlAfterGenerate=types.SimpleNamespace(fixed="fixed"),
            NumberDisplay=types.SimpleNamespace(slider="slider"),
            Hidden=types.SimpleNamespace(unique_id="unique_id"),
        )
        comfy_api = types.ModuleType("comfy_api")
        comfy_api_latest = types.ModuleType("comfy_api.latest")
        comfy_api_latest.io = io_stub
        comfy_exec = types.ModuleType("comfy_execution")
        comfy_exec_graph = types.ModuleType("comfy_execution.graph_utils")
        comfy_exec_graph.ExecutionBlocker = _ExecutionBlocker
        media_stub = types.ModuleType("spicyapi_nodes.media")
        media_stub.decode_video = lambda blob: {"decoded_video": blob}
        media_stub.decode_images = lambda blobs: ({"decoded_images": blobs}, None)
        media_stub.split_image_batch = lambda img: [img] if img is not None else []
        media_stub.encode_image = lambda img: (b"img-bytes", "image/png")
        media_stub.encode_video = lambda vid: (b"vid-bytes", "video/mp4")

        return mock.patch.dict(
            sys.modules,
            {
                "comfy_api": comfy_api,
                "comfy_api.latest": comfy_api_latest,
                "comfy_execution": comfy_exec,
                "comfy_execution.graph_utils": comfy_exec_graph,
                "spicyapi_nodes.media": media_stub,
            },
        )

    @unittest.skipIf(web is None, "aiohttp is not installed")
    async def test_workflow_node_schema_registration_and_video_execution(self):
        with self._install_comfy_stubs():
            sys.modules.pop("spicyapi_nodes.nodes", None)
            import spicyapi_nodes.nodes as nodes_mod

            schema = nodes_mod.SpicyAPIWorkflowRun.define_schema()
            self.assertEqual(schema.node_id, "SpicyAPIWorkflowRun")
            self.assertEqual(schema.category, "SpicyAPI/Workflows")
            self.assertEqual([o.name for o in schema.outputs], ["video", "images", "media_url", "workflow_id", "result_json"])

            built, _ = nodes_mod.build_all([])
            self.assertIn(nodes_mod.SpicyAPIWorkflowRun, built)

            fake_client = mock.AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = None
            fake_client.run_workflow.return_value = {"workflowId": "wfr_99", "state": "queued"}
            fake_client.wait_for_workflow.return_value = {
                "workflowId": "wfr_99",
                "preset": "image-to-video-hd",
                "state": "succeeded",
                "totalCost": "0.12",
                "output": {"assets": [{"mime": "video/mp4", "url": "https://files.test/v.mp4"}]},
            }
            fake_client.download.return_value = b"mp4-data"

            with (
                mock.patch.object(nodes_mod.config, "get_api_key", return_value=("sk-test", "env")),
                mock.patch.object(nodes_mod.config, "get_base_url", return_value="https://api.test"),
                mock.patch.object(nodes_mod, "SpicyClient", return_value=fake_client),
            ):
                out = await nodes_mod.SpicyAPIWorkflowRun.execute(
                    preset="image-to-video-hd",
                    prompt="Cinematic pan",
                    media_url="https://example.com/in.png",
                    resolution="1080p",
                    duration_seconds=5,
                )

            video_out, images_out, media_url_out, workflow_id_out, result_json_out = out.values
            self.assertEqual(video_out, {"decoded_video": b"mp4-data"})
            self.assertEqual(type(images_out).__name__, "_ExecutionBlocker")
            self.assertEqual(media_url_out, "https://files.test/v.mp4")
            self.assertEqual(workflow_id_out, "wfr_99")
            self.assertEqual(json.loads(result_json_out)["totalCost"], "0.12")
            sys.modules.pop("spicyapi_nodes.nodes", None)


if __name__ == "__main__":
    unittest.main()
