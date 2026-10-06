"""The ComfyUI V3 entry point."""

from __future__ import annotations

import asyncio
import logging

from comfy_api.latest import ComfyExtension, io

from . import VERSION, catalog, routes
from .nodes import build_all

log = logging.getLogger("spicyapi")

# Startup must not hang on a slow network: past this, the saved copy of the catalogue is used.
STARTUP_FETCH_SECONDS = 25


class SpicyAPIExtension(ComfyExtension):
    async def on_load(self) -> None:
        routes.register()

    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        try:
            items, source, fetched_at = await asyncio.wait_for(catalog.load(), STARTUP_FETCH_SECONDS)
        except asyncio.TimeoutError:
            log.warning("SpicyAPI: the model list took too long to load; using the saved copy")
            items, source, fetched_at = await catalog.load(allow_live=False)
        nodes, badges = build_all(items)
        routes.STATE.update(
            source=source,
            count=len(nodes),
            fetchedAt=fetched_at,
            badges=badges,
            models={str(item.get("model")) for item in items},
        )
        log.info("SpicyAPI %s: %d nodes from the %s model list", VERSION, len(nodes), source)
        return nodes


async def comfy_entrypoint() -> SpicyAPIExtension:
    return SpicyAPIExtension()
