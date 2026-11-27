"""Managed preset workflow helpers for ComfyUI.

Pure Python on purpose so input validation, preset mapping and status formatting can be tested
without ComfyUI, torch or a network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .fields import MODEL_DEFAULT, OMIT_NUMBER
from .pricing import format_usd

WORKFLOW_PRESETS = (
    "image-to-video-hd",
    "image-to-4k-upscale",
    "video-extend-hd",
)

WORKFLOW_PRESET_MEDIA_KIND: Mapping[str, str] = {
    "image-to-video-hd": "image",
    "image-to-4k-upscale": "image",
    "video-extend-hd": "video",
}

WORKFLOW_PRESET_OUTPUT_KIND: Mapping[str, str] = {
    "image-to-video-hd": "video",
    "image-to-4k-upscale": "image",
    "video-extend-hd": "video",
}

WORKFLOW_ASPECT_RATIOS = (
    MODEL_DEFAULT,
    "16:9",
    "9:16",
    "1:1",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "21:9",
)

WORKFLOW_RESOLUTIONS = (
    MODEL_DEFAULT,
    "720p",
    "1080p",
    "2k",
    "4k",
    "8k",
)

WORKFLOW_AUDIO_OPTIONS = (
    MODEL_DEFAULT,
    "true",
    "false",
)

_ALLOWED_KEYS: Mapping[str, frozenset[str]] = {
    "image-to-video-hd": frozenset(
        {
            "image_url",
            "prompt",
            "edit_prompt",
            "video_prompt",
            "aspect_ratio",
            "duration_seconds",
            "resolution",
            "generate_audio",
        }
    ),
    "image-to-4k-upscale": frozenset(
        {
            "image_url",
            "prompt",
            "aspect_ratio",
            "resolution",
            "output_format",
        }
    ),
    "video-extend-hd": frozenset(
        {
            "video_url",
            "prompt",
            "duration_seconds",
            "extend_resolution",
            "target_resolution",
            "generate_audio",
        }
    ),
}

_RESOLUTIONS_BY_PRESET: Mapping[str, tuple[str, ...]] = {
    "image-to-video-hd": ("720p", "1080p"),
    "image-to-4k-upscale": ("2k", "4k", "8k"),
    "video-extend-hd": ("720p", "1080p", "2k", "4k"),
}

_DURATION_BOUNDS: Mapping[str, tuple[int, int]] = {
    "image-to-video-hd": (3, 15),
    "video-extend-hd": (4, 30),
}


def preset_media_kind(preset: str) -> str:
    """Return 'image' or 'video' for the source media expected by a preset."""
    return WORKFLOW_PRESET_MEDIA_KIND.get(preset.strip(), "image")


def parse_extra_options(raw: str | None) -> dict[str, Any]:
    """Parse optional JSON overrides into a dict."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise ValueError(f"'extra_options_json' must be valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError("'extra_options_json' must be a JSON object.")
    return dict(parsed)


def build_workflow_input(
    preset: str,
    *,
    prompt: str,
    media_url: str = "",
    edit_prompt: str = "",
    video_prompt: str = "",
    aspect_ratio: str = MODEL_DEFAULT,
    resolution: str = MODEL_DEFAULT,
    duration_seconds: int = OMIT_NUMBER,
    generate_audio: str = MODEL_DEFAULT,
    extra_options_json: str = "",
) -> dict[str, Any]:
    """Assemble and validate the ``input`` payload for POST /api/v1/workflows/run."""
    preset = str(preset or "").strip()
    if preset not in _ALLOWED_KEYS:
        supported = ", ".join(WORKFLOW_PRESETS)
        raise ValueError(f"Unknown workflow preset {preset!r}. Expected one of: {supported}.")

    extra = parse_extra_options(extra_options_json)
    payload: dict[str, Any] = {}

    clean_prompt = str(prompt or "").strip()
    if clean_prompt:
        payload["prompt"] = clean_prompt

    media_field = "video_url" if preset_media_kind(preset) == "video" else "image_url"
    clean_media = str(media_url or "").strip()
    if clean_media:
        payload[media_field] = clean_media

    if preset == "image-to-video-hd":
        if str(edit_prompt or "").strip():
            payload["edit_prompt"] = str(edit_prompt).strip()
        if str(video_prompt or "").strip():
            payload["video_prompt"] = str(video_prompt).strip()
        if aspect_ratio and aspect_ratio != MODEL_DEFAULT:
            payload["aspect_ratio"] = aspect_ratio
        if resolution and resolution != MODEL_DEFAULT:
            payload["resolution"] = resolution
        if duration_seconds != OMIT_NUMBER:
            payload["duration_seconds"] = int(duration_seconds)
        if generate_audio in ("true", "false"):
            payload["generate_audio"] = generate_audio == "true"
    elif preset == "image-to-4k-upscale":
        if aspect_ratio and aspect_ratio != MODEL_DEFAULT:
            payload["aspect_ratio"] = aspect_ratio
        if resolution and resolution != MODEL_DEFAULT:
            payload["resolution"] = resolution
    elif preset == "video-extend-hd":
        if resolution and resolution != MODEL_DEFAULT:
            payload["target_resolution"] = resolution
        if duration_seconds != OMIT_NUMBER:
            payload["duration_seconds"] = int(duration_seconds)
        if generate_audio in ("true", "false"):
            payload["generate_audio"] = generate_audio == "true"

    payload.update(extra)

    allowed = _ALLOWED_KEYS[preset]
    unsupported = sorted(key for key in payload if key not in allowed)
    if unsupported:
        names = ", ".join(unsupported)
        raise ValueError(f"Unsupported input field(s) for {preset}: {names}.")

    if not str(payload.get("prompt") or "").strip():
        raise ValueError("'prompt' is required.")

    if not str(payload.get(media_field) or "").strip():
        source_label = "video" if media_field == "video_url" else "image"
        raise ValueError(
            f"Preset '{preset}' requires a source {source_label}. Connect the '{source_label}' socket "
            f"or provide 'media_url'."
        )

    res_key = "target_resolution" if preset == "video-extend-hd" else "resolution"
    if res_key in payload and payload[res_key] is not None:
        valid_res = _RESOLUTIONS_BY_PRESET[preset]
        if payload[res_key] not in valid_res:
            raise ValueError(
                f"'{res_key}' for {preset} must be one of: {', '.join(valid_res)}."
            )

    if "duration_seconds" in payload and payload["duration_seconds"] is not None:
        low, high = _DURATION_BOUNDS[preset]
        dur = payload["duration_seconds"]
        if isinstance(dur, bool) or not isinstance(dur, int) or dur < low or dur > high:
            raise ValueError(
                f"'duration_seconds' for {preset} must be an integer between {low} and {high}."
            )

    if "extend_resolution" in payload and payload["extend_resolution"] not in (None, "480p", "720p"):
        raise ValueError("'extend_resolution' for video-extend-hd must be one of: 480p, 720p.")

    if "output_format" in payload and payload["output_format"] not in (None, "jpeg", "png", "webp"):
        raise ValueError("'output_format' for image-to-4k-upscale must be one of: jpeg, png, webp.")

    return payload


def first_asset_url(record: Mapping[str, Any]) -> str:
    """Return the first deliverable asset URL from a workflow run response, or an empty string."""
    output = record.get("output")
    if not isinstance(output, Mapping):
        return ""
    assets = output.get("assets")
    if not isinstance(assets, list):
        return ""
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        if asset.get("pending") or asset.get("unavailable"):
            continue
        url = str(asset.get("url") or "").strip()
        if url:
            return url
    return ""


def workflow_status_line(record: Mapping[str, Any], elapsed: float) -> str:
    """Compact progress line shown on the ComfyUI node while polling."""
    workflow_id = str(record.get("workflowId") or "")
    state = str(record.get("state") or "running")
    steps = record.get("steps")
    total_steps = int(record.get("totalSteps") or (len(steps) if isinstance(steps, list) else 0))
    current_index = int(record.get("currentStep") or 0)
    step_title = ""
    if isinstance(steps, list) and 0 <= current_index < len(steps):
        current = steps[current_index]
        if isinstance(current, Mapping):
            step_title = str(current.get("title") or current.get("key") or "")
    label = {"queued": "Queued", "running": "Running"}.get(state, state.capitalize())
    step_part = ""
    if total_steps > 0:
        display_step = min(current_index + 1, total_steps)
        step_part = f"Step {display_step}/{total_steps}"
        if step_title:
            step_part += f" ({step_title})"
        step_part += ": "
    line = f"{step_part}{label} {int(elapsed)}s"
    return f"{line}\nWorkflow {workflow_id}" if workflow_id else line


def workflow_done_line(record: Mapping[str, Any], elapsed: float) -> str:
    """Summary line shown once the managed workflow finishes."""
    workflow_id = str(record.get("workflowId") or "")
    cost = record.get("totalCost")
    cost_part = f" Total {format_usd(cost)}" if cost not in (None, "") else ""
    line = f"Done in {int(elapsed)}s.{cost_part}"
    return f"{line}\nWorkflow {workflow_id}" if workflow_id else line
