"""The model list that the nodes are generated from.

ComfyUI defines node types once, at startup, so the catalogue is resolved then, in this order:

1. live from the API, when a key is configured (and the result is cached on disk);
2. the cache from the last successful live fetch;
3. the snapshot shipped with this package, so the nodes exist before a key is set.

A model added to SpicyAPI therefore shows up after the next ComfyUI restart, with no update to
this package.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("spicyapi")

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog-snapshot.json"

# Only what the nodes use; the full catalogue entry carries much more.
KEPT_FIELDS = (
    "model",
    "displayName",
    "familyDisplayName",
    "provider",
    "modality",
    "tasks",
    "inputSchema",
    "pricing",
    "startingPrice",
    "taskTimeoutSeconds",
    "policyTier",
    "enabled",
    "summary",
    "familyPageSlug",
)


def slim(items: list[dict[str, Any]], public: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep what the nodes use, plus the summary and page slug from the public catalogue."""
    kept: list[dict[str, Any]] = []
    for item in items:
        model_id = item.get("model")
        if not isinstance(model_id, str) or not isinstance(item.get("inputSchema"), dict):
            continue
        entry = {key: item[key] for key in KEPT_FIELDS if key in item}
        for key in ("summary", "familyPageSlug"):
            value = (public.get(model_id) or {}).get(key) or item.get(key)
            if value:
                entry[key] = value
        kept.append(entry)
    # The API lists models newest first; that order is kept for menus and the chat model list.
    return kept


def _cache_path() -> Path:
    return config.config_dir() / "catalog-cache.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    return data


def write_cache(items: list[dict[str, Any]]) -> None:
    directory = config.config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"fetchedAt": int(time.time()), "items": items}, handle)
        os.replace(temp_path, _cache_path())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise


def cached_public_fields() -> dict[str, dict[str, Any]]:
    """Summaries and page slugs from the snapshot and the cache, for when the public list fails."""
    known: dict[str, dict[str, Any]] = {}
    for source in (_read_json(SNAPSHOT_PATH), _read_json(_cache_path())):
        for item in (source or {}).get("items") or []:
            if isinstance(item, dict) and item.get("model"):
                fields = {k: item[k] for k in ("summary", "familyPageSlug") if item.get(k)}
                known.setdefault(item["model"], {}).update(fields)
    return known


async def fetch_live(api_key: str) -> list[dict[str, Any]]:
    """Fetch the authenticated catalogue (with schemas) plus the public one-line summaries."""
    from .client import SpicyClient

    async with SpicyClient(api_key, config.get_base_url()) as client:
        items = await client.list_models()
        public = cached_public_fields()
        try:
            for entry in await client.public_catalog():
                if entry.get("model"):
                    fields = {k: entry[k] for k in ("summary", "familyPageSlug") if entry.get(k)}
                    public.setdefault(entry["model"], {}).update(fields)
        except Exception as error:  # summaries and links are decoration; never fail the load on them
            log.info("SpicyAPI: model summaries unavailable (%s)", error)
    return slim(items, public)


async def load(*, allow_live: bool = True) -> tuple[list[dict[str, Any]], str, int | None]:
    """Return (items, source, fetchedAt). Source is live, cache, snapshot or empty."""
    api_key, _ = config.get_api_key()
    if api_key and allow_live:
        try:
            items = await fetch_live(api_key)
            if items:
                try:
                    write_cache(items)
                except OSError as error:
                    log.warning("SpicyAPI: could not write the catalogue cache: %s", error)
                return items, "live", int(time.time())
        except Exception as error:
            log.warning("SpicyAPI: could not load the live model list, using a saved copy (%s)", error)
    cached = _read_json(_cache_path())
    if cached and cached["items"]:
        return cached["items"], "cache", cached.get("fetchedAt")
    snapshot = _read_json(SNAPSHOT_PATH)
    if snapshot and snapshot["items"]:
        return snapshot["items"], "snapshot", snapshot.get("fetchedAt")
    return [], "empty", None
