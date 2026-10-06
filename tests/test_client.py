"""The HTTP client against a local fake of the API. Needs aiohttp, which ComfyUI ships."""

import os
import unittest

try:
    from aiohttp import web
    from aiohttp.test_utils import TestServer
except ImportError:  # pragma: no cover - only when run outside a ComfyUI environment
    web = None

if web is not None:
    from spicyapi_nodes.client import SpicyApiError, SpicyClient


def envelope(data, code=200, status=200, **headers):
    body = {"code": code, "msg": "success" if code == 200 else f"failed {code}", "data": data, "request_id": "req_test"}
    return web.json_response(body, status=status, headers=headers)


@unittest.skipIf(web is None, "aiohttp is not installed")
class ClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # The client honours HTTP(S)_PROXY like any aiohttp client; the fake server is local.
        for name in ("NO_PROXY", "no_proxy"):
            previous = os.environ.get(name)
            os.environ[name] = "127.0.0.1,localhost"
            self.addCleanup(lambda n=name, v=previous: os.environ.pop(n) if v is None else os.environ.__setitem__(n, v))
        self.calls = {}
        self.seen_keys = []
        app = web.Application()
        app.router.add_get("/api/v1/models", self.models)
        app.router.add_post("/api/v1/jobs/createTask", self.create)
        app.router.add_post("/api/v1/common/upload-url", self.ticket)
        app.router.add_put("/storage/object", self.put)
        app.router.add_post("/api/v1/files/{file_id}/commit", self.commit)
        app.router.add_get("/api/v1/jobs/recordInfo", self.record)
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

    async def models(self, request):
        assert request.headers["Authorization"] == "Bearer sk-test"
        assert request.headers["User-Agent"].startswith("SpicyAPI-ComfyUI/")
        if self.count("models") == 1:
            return envelope(None, code=503, status=503, **{"Retry-After": "0"})
        return envelope({"items": [{"model": "a/b/c"}], "total": 1})

    async def create(self, request):
        self.seen_keys.append(request.headers.get("Idempotency-Key"))
        body = await request.json()
        if body["model"] == "always/refunded/x":
            return envelope(None, code=50302, status=503)
        if self.count("create") == 1:
            return envelope(None, code=503, status=503, **{"Retry-After": "0"})
        return envelope({"taskId": "job_1", "state": "queued"})

    async def ticket(self, request):
        self.count("ticket")
        body = await request.json()
        return envelope(
            {
                "fileId": "fil_1",
                "uploadUrl": f"{self.base}/storage/object",
                "method": "PUT",
                "headers": {"Content-Type": body["contentType"], "X-Signed": "yes"},
                "maxBytes": 1024,
            }
        )

    async def put(self, request):
        data = await request.read()
        ok = (
            request.headers.get("X-Signed") == "yes"
            and request.headers.get("Content-Type") == "image/png"
            and "Authorization" not in request.headers
            and data == b"\x89PNG-bytes"
        )
        return web.Response(status=200 if ok else 403)

    async def commit(self, request):
        return envelope({"uri": f"spicy://f/{request.match_info['file_id']}"})

    async def record(self, request):
        n = self.count("record")
        asset = {"mime": "image/png", "pending": True} if n == 1 else {"mime": "image/png", "url": "https://x/y.png"}
        return envelope({"taskId": "job_1", "state": "succeeded", "output": {"assets": [asset]}})

    async def test_retries_a_503_then_succeeds(self):
        items = await self.client.list_models()
        self.assertEqual(items, [{"model": "a/b/c"}])
        self.assertEqual(self.calls["models"], 2)

    async def test_resend_keeps_the_idempotency_key(self):
        task = await self.client.create_task("m/x/y", {"prompt": "p"}, idempotency_key="key-1")
        self.assertEqual(task["taskId"], "job_1")
        self.assertEqual(self.seen_keys, ["key-1", "key-1"])

    async def test_refunded_failure_is_not_replayed(self):
        with self.assertRaises(SpicyApiError) as caught:
            await self.client.create_task("always/refunded/x", {}, idempotency_key="key-2")
        self.assertEqual(caught.exception.code, 50302)
        self.assertEqual(self.seen_keys, ["key-2"])
        self.assertIn("refunded", caught.exception.recovery)

    async def test_upload_sends_ticket_headers_and_no_key(self):
        uri = await self.client.upload(b"\x89PNG-bytes", "image/png")
        self.assertEqual(uri, "spicy://f/fil_1")

    async def test_upload_refuses_oversized_bytes(self):
        with self.assertRaises(SpicyApiError):
            await self.client.upload(b"x" * 2048, "image/png")

    async def test_wait_skips_pending_assets(self):
        task = await self.client.wait_for_task("job_1", timeout_seconds=30)
        self.assertEqual(task["output"]["assets"][0]["url"], "https://x/y.png")
        self.assertEqual(self.calls["record"], 2)


if __name__ == "__main__":
    unittest.main()
