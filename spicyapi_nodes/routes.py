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


def _key_was_refused(error: SpicyApiError) -> bool:
    """Only 401 means the key itself is bad.

    Saving a key checks it by reading the balance. A 403 can be a valid key without the
    read_balance permission, and that key still runs models, so it must not be turned away.
    """
    return error.status == 401


# The settings panel sends this header with every change. A plain cross-site form or a "simple"
# request cannot add a custom header, so requiring it keeps another web page from changing the key
# or the spending ceiling even when ComfyUI runs with --enable-cors-header (which turns off its own
# cross-origin check). It is not a password: anyone who can reach the ComfyUI port can still send it,
# which is what SPICYAPI_LOCK_SETTINGS is for.
SETTINGS_HEADER = "X-SpicyAPI-Settings"


def _refuse_change(request: web.Request, *, needs_json: bool) -> web.Response | None:
    """Return an error response when a settings change must not go through, otherwise None."""
    if config.settings_locked():
        return web.json_response(
            {
                "ok": False,
                "message": "SpicyAPI settings are locked on this server (SPICYAPI_LOCK_SETTINGS). "
                "Set the key with the SPICY_API_KEY environment variable instead.",
            },
            status=403,
        )
    if request.headers.get(SETTINGS_HEADER) != "1":
        return web.json_response({"ok": False, "message": "Change this from Settings > SpicyAPI."}, status=403)
    if needs_json and request.content_type != "application/json":
        return web.json_response({"ok": False, "message": "Send JSON."}, status=415)
    return None


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
        if (refused := _refuse_change(request, needs_json=True)) is not None:
            return refused
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
            if _key_was_refused(error):
                return web.json_response(
                    {"ok": False, "message": "SpicyAPI rejected this key. Copy it again from the console."},
                    status=400,
                )
            if error.status != 403:
                # Could not reach SpicyAPI or it failed: we cannot tell whether this key is real,
                # so keep the key that is already in use instead of storing an unchecked one.
                log.warning("SpicyAPI: could not check the key, not saving it: %s", error)
                return web.json_response(
                    {"ok": False, "message": "Could not reach SpicyAPI to check this key. Try again in a moment."},
                    status=502,
                )
            # 403: a valid key without the read_balance permission. It runs models, so keep it.
            verified = False
        config.save_api_key(api_key)
        result: dict[str, Any] = {"ok": True, "verified": verified, **_status_payload()}
        if balance:
            result["balance"] = {k: balance.get(k) for k in ("available", "held", "total")}
        result["catalogRefresh"] = await _refresh_catalog(api_key)
        return web.json_response(result)

    @routes.delete("/spicyapi/api-key")
    async def delete_key(request: web.Request) -> web.Response:
        if (refused := _refuse_change(request, needs_json=False)) is not None:
            return refused
        config.clear_api_key()
        return web.json_response({"ok": True, **_status_payload()})

    @routes.post("/spicyapi/settings")
    async def save_settings(request: web.Request) -> web.Response:
        if (refused := _refuse_change(request, needs_json=True)) is not None:
            return refused
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
