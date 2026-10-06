"""HTTP routes the settings panel and the price badges talk to.

They are served by the ComfyUI server itself. None of them ever returns the key: the status
route reports where a key came from and its last four characters, nothing more.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from . import VERSION, catalog, config
from .client import SpicyApiError, SpicyClient

log = logging.getLogger("spicyapi")

# Filled in by the extension once the nodes are built.
STATE: dict[str, Any] = {"source": "empty", "count": 0, "fetchedAt": None, "badges": {}, "models": set()}

_registered = False


def _status_payload() -> dict[str, Any]:
    api_key, source = config.get_api_key()
    ceiling = config.get_max_cost_per_run()
    return {
        "version": VERSION,
        "configured": bool(api_key),
        "keySource": source,
        "keyHint": config.key_hint(api_key),
        "maxCostPerRun": str(ceiling) if ceiling is not None else "0",
        "catalog": {
            "source": STATE["source"],
            "count": STATE["count"],
            "fetchedAt": STATE["fetchedAt"],
        },
    }


async def _refresh_catalog(api_key: str) -> dict[str, Any]:
    """Fetch the live list now so the next start has it; report whether a restart adds nodes."""
    try:
        items = await catalog.fetch_live(api_key)
    except Exception as error:
        return {"refreshed": False, "message": str(error)}
    catalog.write_cache(items)
    live = {item["model"] for item in items}
    added = sorted(live - STATE["models"])
    return {
        "refreshed": True,
        "count": len(items),
        "added": added,
        "restartNeeded": bool(added) or STATE["source"] == "snapshot",
    }


def register() -> None:
    global _registered
    if _registered:
        return
    from server import PromptServer

    routes = PromptServer.instance.routes

    @routes.get("/spicyapi/status")
    async def status(_: web.Request) -> web.Response:
        return web.json_response(_status_payload())

    @routes.get("/spicyapi/prices")
    async def prices(_: web.Request) -> web.Response:
        return web.json_response(STATE["badges"], headers={"Cache-Control": "no-store"})

    @routes.post("/spicyapi/api-key")
    async def save_key(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"ok": False, "message": "Send JSON."}, status=400)
        api_key = str((body or {}).get("apiKey") or "").strip()
        if not api_key or any(ch.isspace() for ch in api_key) or len(api_key) > 256:
            return web.json_response({"ok": False, "message": "That does not look like an API key."}, status=400)
        balance: dict[str, Any] | None = None
        verified = True
        try:
            async with SpicyClient(api_key, config.get_base_url()) as client:
                balance = await client.balance()
        except SpicyApiError as error:
            if error.status in (401, 403):
                return web.json_response(
                    {"ok": False, "message": "SpicyAPI rejected this key. Copy it again from the console."},
                    status=400,
                )
            verified = False
            log.warning("SpicyAPI: saved the key without verifying it: %s", error)
        config.save_api_key(api_key)
        result: dict[str, Any] = {"ok": True, "verified": verified, **_status_payload()}
        if balance:
            result["balance"] = {k: balance.get(k) for k in ("available", "held", "total")}
        result["catalogRefresh"] = await _refresh_catalog(api_key)
        return web.json_response(result)

    @routes.delete("/spicyapi/api-key")
    async def delete_key(_: web.Request) -> web.Response:
        config.clear_api_key()
        return web.json_response({"ok": True, **_status_payload()})

    @routes.post("/spicyapi/settings")
    async def save_settings(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"ok": False, "message": "Send JSON."}, status=400)
        if "maxCostPerRun" in (body or {}):
            try:
                config.save_max_cost_per_run(body["maxCostPerRun"])
            except ValueError as error:
                return web.json_response({"ok": False, "message": str(error)}, status=400)
        return web.json_response({"ok": True, **_status_payload()})

    @routes.get("/spicyapi/balance")
    async def balance(_: web.Request) -> web.Response:
        api_key, _source = config.get_api_key()
        if not api_key:
            return web.json_response({"ok": False, "message": "No key is set."}, status=400)
        try:
            async with SpicyClient(api_key, config.get_base_url()) as client:
                data = await client.balance()
        except SpicyApiError as error:
            return web.json_response({"ok": False, "message": str(error)}, status=502)
        return web.json_response({"ok": True, **{k: data.get(k) for k in ("available", "held", "total")}})

    _registered = True
