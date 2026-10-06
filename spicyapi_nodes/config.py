"""Where the API key and local settings live.

The key never goes into a node widget. Widget values are saved inside every workflow file and
inside the metadata of every image ComfyUI writes, so a key typed into a node would leak the
first time someone shares a picture. It is read from, in order:

1. the ``SPICY_API_KEY`` environment variable;
2. ``<ComfyUI user directory>/spicyapi/config.json``, written by the settings panel.

The config file is created with owner-only permissions where the platform supports it.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.spicyapi.ai"
ENV_API_KEY = "SPICY_API_KEY"
ENV_BASE_URL = "SPICY_API_BASE_URL"
ENV_CONFIG_DIR = "SPICYAPI_COMFYUI_HOME"


def config_dir() -> Path:
    """Directory for the key, the settings and the catalogue cache."""
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override)
    try:
        import folder_paths  # ComfyUI module; absent when the tests run on their own

        return Path(folder_paths.get_user_directory()) / "spicyapi"
    except Exception:
        return Path.home() / ".spicyapi" / "comfyui"


def _config_path() -> Path:
    return config_dir() / "config.json"


def _read() -> dict[str, Any]:
    try:
        with open(_config_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file and rename it, so a crash halfway through never leaves a
    # truncated config behind (that would silently drop the key on the next start).
    fd, temp_path = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        with contextlib.suppress(OSError):
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, _config_path())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise


def get_api_key() -> tuple[str, str]:
    """The key and where it came from: ``env``, ``file`` or ``none``."""
    env_value = os.environ.get(ENV_API_KEY, "").strip()
    if env_value:
        return env_value, "env"
    file_value = str(_read().get("apiKey") or "").strip()
    if file_value:
        return file_value, "file"
    return "", "none"


def save_api_key(api_key: str) -> None:
    data = _read()
    data["apiKey"] = api_key.strip()
    _write(data)


def clear_api_key() -> None:
    data = _read()
    data.pop("apiKey", None)
    _write(data)


def key_hint(api_key: str) -> str:
    """A form of the key that is safe to show: the last four characters only."""
    if not api_key:
        return ""
    return f"...{api_key[-4:]}" if len(api_key) > 8 else "..."


def get_base_url() -> str:
    value = os.environ.get(ENV_BASE_URL, "").strip() or DEFAULT_BASE_URL
    return value.rstrip("/")


def get_max_cost_per_run() -> Decimal | None:
    """Spending ceiling for one node run, or None when no ceiling is set."""
    raw = _read().get("maxCostPerRun")
    if raw in (None, "", 0, "0"):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def save_max_cost_per_run(value: Any) -> Decimal | None:
    data = _read()
    try:
        parsed = Decimal(str(value)) if value not in (None, "") else Decimal(0)
    except InvalidOperation as error:
        raise ValueError("maxCostPerRun must be a number of US dollars") from error
    if parsed < 0:
        raise ValueError("maxCostPerRun must not be negative")
    data["maxCostPerRun"] = str(parsed)
    _write(data)
    return parsed if parsed > 0 else None
