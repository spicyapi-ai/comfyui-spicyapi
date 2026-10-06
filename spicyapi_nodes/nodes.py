"""ComfyUI node classes: one per media model, plus Chat and LoRA helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from comfy_api.latest import io

from . import config, media
from .client import (
    SpicyApiError,
    SpicyClient,
    SpicyTaskFailed,
    output_assets,
    output_text,
)
from .fields import (
    OMIT,
    FieldSpec,
    OutputPlan,
    build_fields,
    category_for,
    model_page,
    node_id_for,
    plan_outputs,
    widget_to_value,
)
from .pricing import badge_for, describe_price, format_usd

log = logging.getLogger("spicyapi")

LORAS = io.Custom("SPICY_LORAS")

NO_KEY_MESSAGE = (
    "No SpicyAPI key is set. Open Settings > SpicyAPI and paste a key from "
    "https://spicyapi.ai/console/keys, or set the SPICY_API_KEY environment variable, then run "
    "the workflow again."
)
UPLOAD_CACHE_SECONDS = 12 * 3600
# Fast, inexpensive and able to read images; the first one still in the catalogue is preselected.
CHAT_DEFAULTS = ("google/gemini-3.7-flash/chat", "google/gemini-3-flash-preview/chat", "deepseek/v4.1-flash/chat")
DEFAULT_TASK_TIMEOUT_SECONDS = 3600

# sha256 of the bytes, per account -> (spicy:// URI, uploaded at). Uploaded files are kept for a
# day, so re-running a workflow with a different prompt does not send the same picture again.
_upload_cache: dict[tuple[str, str], tuple[str, float]] = {}


def _interrupt_check() -> None:
    import comfy.model_management

    comfy.model_management.throw_exception_if_processing_interrupted()


def _show(node_id: str | None, text: str) -> None:
    if not node_id:
        return
    try:
        from server import PromptServer

        PromptServer.instance.send_progress_text(text, node_id)
    except Exception:  # the status line is a courtesy; never fail a run over it
        pass


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def _upload(client: SpicyClient, data: bytes, content_type: str) -> str:
    account = hashlib.sha256(client.api_key.encode()).hexdigest()[:16]
    digest = hashlib.sha256(data).hexdigest()
    cached = _upload_cache.get((account, digest))
    if cached and time.time() - cached[1] < UPLOAD_CACHE_SECONDS:
        return cached[0]
    uri = await client.upload(data, content_type)
    _upload_cache[(account, digest)] = (uri, time.time())
    return uri


def _content_type_for_path(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    suffix = os.path.splitext(path)[1].lower()
    overrides = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".key": "application/vnd.apple.keynote",
        ".pages": "application/vnd.apple.pages",
        ".numbers": "application/vnd.apple.numbers",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
    }
    return overrides.get(suffix) or guessed or "application/octet-stream"


def _input_file(name: str) -> str:
    """Resolve a file name inside ComfyUI's input folder, and nowhere else.

    Workflows are shared freely, so a path typed into a node must not be able to reach outside
    the folder meant for inputs (a workflow pointing at ~/.ssh would otherwise upload it).
    """
    import folder_paths

    base = os.path.realpath(folder_paths.get_input_directory())
    candidate = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, candidate]) != base:
        raise ValueError(
            f"{name} is outside ComfyUI's input folder. Put the file in {base} and give its name, "
            "or use an https:// link."
        )
    if not os.path.isfile(candidate):
        raise ValueError(f"{name} was not found in ComfyUI's input folder ({base}).")
    return candidate


def _socket_values(value: Any) -> list[Any]:
    """Values connected to an Autogrow group, in socket order."""
    if value is None:
        return []
    if isinstance(value, dict):

        def index(key: str) -> int:
            match = re.search(r"(\d+)$", key)
            return int(match.group(1)) if match else 0

        return [value[k] for k in sorted(value, key=index) if value[k] is not None]
    return [value]


async def _field_value(client: SpicyClient, spec: FieldSpec, raw: Any) -> Any:
    if spec.kind == "image":
        pictures = media.split_image_batch(raw)
        if not pictures:
            return OMIT
        if len(pictures) > 1:
            log.info("SpicyAPI: '%s' takes one image; using the first of %d", spec.label, len(pictures))
        return await _upload(client, *media.encode_image(pictures[0]))
    if spec.kind == "images":
        pictures = [p for value in _socket_values(raw) for p in media.split_image_batch(value)]
        if not pictures:
            return OMIT
        return [await _upload(client, *media.encode_image(p)) for p in pictures]
    if spec.kind == "video":
        return OMIT if raw is None else await _upload(client, *media.encode_video(raw))
    if spec.kind == "videos":
        clips = _socket_values(raw)
        return [await _upload(client, *media.encode_video(c)) for c in clips] if clips else OMIT
    if spec.kind == "audio":
        return OMIT if raw is None else await _upload(client, *media.encode_audio(raw))
    if spec.kind == "audios":
        clips = _socket_values(raw)
        return [await _upload(client, *media.encode_audio(c)) for c in clips] if clips else OMIT
    if spec.kind == "loras":
        return list(raw) if raw else OMIT
    if spec.kind == "file":
        text = str(raw or "").strip()
        if not text:
            return OMIT
        if text.startswith(("http://", "https://", "spicy://")):
            return text
        path = _input_file(text)
        with open(path, "rb") as handle:
            data = handle.read()
        return await _upload(client, data, _content_type_for_path(path))
    value = widget_to_value(spec, raw)
    if value is not OMIT and spec.kind == "string" and spec.required and not str(value).strip():
        raise ValueError(f"'{spec.label}' is required")
    return value


def _input_for(spec: FieldSpec) -> io.Input:
    common = {"tooltip": spec.tooltip or None}
    optional = not spec.required
    label = spec.label if spec.label != spec.name else None
    if spec.kind in ("image", "video", "audio"):
        socket = {"image": io.Image, "video": io.Video, "audio": io.Audio}[spec.kind]
        return socket.Input(spec.name, display_name=label, optional=optional, **common)
    if spec.kind in ("images", "videos", "audios"):
        socket = {"images": io.Image, "videos": io.Video, "audios": io.Audio}[spec.kind]
        # Sockets are named after the field (reference_image_0, reference_image_1...), since the
        # group label itself is not drawn on the node.
        stem = re.sub(r"_urls?$", "", spec.name)
        prefix = f"{stem}_"
        return io.Autogrow.Input(
            spec.name,
            template=io.Autogrow.TemplatePrefix(
                socket.Input(stem, optional=spec.min_items == 0),
                prefix=prefix,
                min=spec.min_items,
                max=spec.max_items,
            ),
            display_name=label,
            optional=optional,
            **common,
        )
    if spec.kind == "loras":
        return LORAS.Input(spec.name, display_name=label, optional=True, **common)
    if spec.kind in ("int_socket", "float_socket"):
        number = io.Int if spec.kind == "int_socket" else io.Float
        return number.Input(spec.name, display_name=label, optional=True, force_input=True, **common)
    widget = {"advanced": spec.advanced or None, "display_name": label, **common}
    if spec.kind == "combo":
        return io.Combo.Input(spec.name, options=spec.options, default=spec.default, **widget)
    if spec.kind == "bool":
        return io.Boolean.Input(spec.name, default=bool(spec.default), **widget)
    if spec.kind == "int":
        extra: dict[str, Any] = {}
        if spec.seed:
            # Fixed, not randomize: every run is billed, so queueing the same workflow twice
            # should reuse the cached result instead of paying for a second one by surprise.
            extra["control_after_generate"] = io.ControlAfterGenerate.fixed
        if spec.slider:
            extra["display_mode"] = io.NumberDisplay.slider
        return io.Int.Input(
            spec.name,
            default=int(spec.default),
            min=int(spec.minimum) if spec.minimum is not None else -(2**31),
            max=int(spec.maximum) if spec.maximum is not None else 2**31 - 1,
            step=int(spec.step or 1),
            **extra,
            **widget,
        )
    if spec.kind == "float":
        extra = {"display_mode": io.NumberDisplay.slider} if spec.slider else {}
        step = float(spec.step or 0.01)
        return io.Float.Input(
            spec.name,
            default=float(spec.default),
            min=float(spec.minimum) if spec.minimum is not None else -1e9,
            max=float(spec.maximum) if spec.maximum is not None else 1e9,
            step=step,
            round=step,
            **extra,
            **widget,
        )
    if spec.kind in ("string", "file", "json"):
        placeholder = "File name in ComfyUI/input, or an https:// link" if spec.kind == "file" else None
        return io.String.Input(
            spec.name,
            default=str(spec.default or ""),
            multiline=spec.multiline,
            placeholder=placeholder,
            **widget,
        )
    raise ValueError(f"unhandled field kind {spec.kind}")


def _outputs_for(plan: OutputPlan) -> list[io.Output]:
    task_id = io.String.Output(
        "task_id", display_name="task id", tooltip="The SpicyAPI task id, for the console logs or support."
    )
    if plan.primary == "image":
        outputs: list[io.Output] = [io.Image.Output("images", display_name="images")]
        if plan.alpha:
            outputs.append(
                io.Mask.Output("alpha", display_name="alpha", tooltip="Transparency of each image: 1 is opaque.")
            )
        return [*outputs, task_id]
    if plan.primary == "video":
        outputs = [io.Video.Output("video", display_name="video")]
        if plan.last_frame:
            outputs.append(
                io.Image.Output("last_frame", display_name="last frame", tooltip="Needs return_last_frame turned on.")
            )
        return [*outputs, task_id]
    if plan.primary == "audio":
        return [
            io.Audio.Output(
                "audio",
                display_name="audio",
                tooltip="Several results come back as a batch; Save Audio writes one file each.",
            ),
            task_id,
        ]
    if plan.primary == "text":
        return [io.String.Output("text", display_name="text"), task_id]
    return [
        io.Image.Output("previews", display_name="previews"),
        io.String.Output("text", display_name="text"),
        task_id,
    ]


async def _download_all(
    client: SpicyClient, assets: list[dict[str, Any]], prefix: str, limit: int | None = None
) -> list[bytes]:
    wanted = [a for a in assets if str(a.get("mime") or "").startswith(prefix) and a.get("url")]
    if limit is not None:
        wanted = wanted[:limit]
    return list(await asyncio.gather(*(client.download(str(a["url"])) for a in wanted)))


async def _collect_outputs(client: SpicyClient, plan: OutputPlan, task: dict[str, Any]) -> list[Any]:
    from comfy_execution.graph_utils import ExecutionBlocker

    task_id = str(task.get("taskId") or "")
    assets = output_assets(task)
    if plan.primary == "image":
        blobs = await _download_all(client, assets, "image/")
        images, alpha = media.decode_images(blobs)
        return [images, alpha, task_id] if plan.alpha else [images, task_id]
    if plan.primary == "video":
        videos = await _download_all(client, assets, "video/", limit=1)
        if not videos:
            raise ValueError(f"task {task_id} finished without a video")
        result: list[Any] = [media.decode_video(videos[0])]
        if plan.last_frame:
            frames = await _download_all(client, assets, "image/", limit=1)
            result.append(
                media.decode_images(frames)[0]
                if frames
                else ExecutionBlocker("Turn on return_last_frame on the SpicyAPI node to get the last frame.")
            )
        return [*result, task_id]
    if plan.primary == "audio":
        clips = [media.decode_audio(blob) for blob in await _download_all(client, assets, "audio/")]
        if not clips:
            raise ValueError(f"task {task_id} finished without audio")
        return [_audio_batch(clips), task_id]
    if plan.primary == "text":
        return [_task_text(task), task_id]
    blobs = await _download_all(client, assets, "image/")
    previews = media.decode_images(blobs)[0] if blobs else media.empty_image()
    return [previews, _task_text(task), task_id]


def _task_text(task: dict[str, Any]) -> str:
    text = output_text(task)
    if text is not None:
        return text
    output = task.get("output")
    if isinstance(output, dict):
        rest = {k: v for k, v in output.items() if k != "assets"}
        if rest:
            return json.dumps(rest, ensure_ascii=False, indent=2)
    return ""


def _audio_batch(clips: list[dict[str, Any]]) -> dict[str, Any]:
    """Several clips as one AUDIO batch, padded to the longest and matched on sample rate."""
    import torch

    rate = clips[0]["sample_rate"]
    channels = max(c["waveform"].shape[1] for c in clips)
    waves = []
    for clip in clips:
        wave = clip["waveform"][0]
        if clip["sample_rate"] != rate:
            length = round(wave.shape[1] * rate / clip["sample_rate"])
            wave = torch.nn.functional.interpolate(wave.unsqueeze(0), size=length, mode="linear", align_corners=False)[
                0
            ]
        if wave.shape[0] < channels:
            wave = wave.repeat(channels // wave.shape[0], 1)[:channels]
        waves.append(wave)
    length = max(w.shape[1] for w in waves)
    padded = [torch.nn.functional.pad(w, (0, length - w.shape[1])) for w in waves]
    return {"waveform": torch.stack(padded), "sample_rate": rate}


async def _quote_within_limit(client: SpicyClient, model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Quote the run and stop, before anything is charged, if it could exceed the user's limit."""
    quote = await client.quote(model_id, payload)
    ceiling = config.get_max_cost_per_run()
    worst = _money(quote.get("maxCharge")) or _money(quote.get("estimatedCost"))
    if ceiling is not None and worst is not None and worst > ceiling:
        raise ValueError(
            f"This run could cost up to {format_usd(worst)}, above your limit of "
            f"{format_usd(ceiling)} per run (Settings > SpicyAPI). Nothing was charged."
        )
    return quote


async def run_model(
    model: dict[str, Any],
    specs: list[FieldSpec],
    plan: OutputPlan,
    node_id: str | None,
    values: dict[str, Any],
) -> list[Any]:
    api_key, _ = config.get_api_key()
    if not api_key:
        raise ValueError(NO_KEY_MESSAGE)
    model_id = str(model["model"])
    started = time.monotonic()
    async with SpicyClient(api_key, config.get_base_url(), interrupt_check=_interrupt_check) as client:
        _show(node_id, "Preparing inputs...")
        payload: dict[str, Any] = {}
        for spec in specs:
            value = await _field_value(client, spec, values.get(spec.name))
            if value is not OMIT:
                payload[spec.name] = value

        quote = await _quote_within_limit(client, model_id, payload)
        estimated = str(quote.get("estimatedCost") or "")
        _show(node_id, f"Submitting. Estimated {format_usd(estimated)}")

        idempotency_key = str(uuid.uuid4())
        try:
            task = await client.create_task(
                model_id,
                payload,
                idempotency_key=idempotency_key,
                quote_id=quote.get("quoteId"),
                expected_cost=estimated or None,
            )
        except SpicyApiError as error:
            if error.code != 40901:
                raise
            # The price moved between quote and submit: quote again and resend under the same
            # key, so a task the server did accept is recovered rather than created twice.
            quote = await _quote_within_limit(client, model_id, payload)
            estimated = str(quote.get("estimatedCost") or "")
            task = await client.create_task(
                model_id,
                payload,
                idempotency_key=idempotency_key,
                quote_id=quote.get("quoteId"),
                expected_cost=estimated or None,
            )
        task_id = str(task.get("taskId") or "")

        def on_poll(record: dict[str, Any], elapsed: float) -> None:
            state = str(record.get("state") or "")
            label = {"queued": "Queued", "running": "Generating"}.get(state, state.capitalize())
            _show(node_id, f"{label} {int(elapsed)}s. Estimated {format_usd(estimated)}\nTask {task_id}")

        timeout = float(model.get("taskTimeoutSeconds") or DEFAULT_TASK_TIMEOUT_SECONDS) + 300
        try:
            final = await client.wait_for_task(task_id, timeout_seconds=timeout, on_poll=on_poll)
        except BaseException as error:
            if type(error).__name__ == "InterruptProcessingException":
                log.warning(
                    "SpicyAPI: stopped waiting for task %s. It keeps running on SpicyAPI and is "
                    "charged if it succeeds; its result stays available in the console logs.",
                    task_id,
                )
                _show(node_id, f"Stopped waiting. Task {task_id} keeps running on SpicyAPI.")
            raise
        if final.get("state") != "succeeded":
            _show(node_id, f"Failed: {final.get('errorCode') or final.get('state')}\nTask {task_id}")
            raise SpicyTaskFailed(final)

        _show(node_id, "Downloading result...")
        outputs = await _collect_outputs(client, plan, final)
        cost = final.get("cost")
        verb = "Charged" if final.get("settled") else "Held"
        _show(
            node_id,
            f"Done in {int(time.monotonic() - started)}s. {verb} {format_usd(cost)}\nTask {task_id}",
        )
        return outputs


def make_model_node(model: dict[str, Any]) -> type[io.ComfyNode]:
    specs = build_fields(model.get("inputSchema") or {})
    plan = plan_outputs(model)
    node_id = node_id_for(str(model["model"]))
    badge = badge_for(model)
    description = "\n\n".join(
        part
        for part in (
            str(model.get("summary") or "").strip(),
            describe_price(badge),
            f"Model: {model['model']}",
            f"Docs and examples: {model_page(model)}" if model.get("familyPageSlug") else "",
        )
        if part
    )
    aliases = [str(model["model"]), "spicyapi"]
    family = model.get("familyDisplayName")
    if family:
        aliases.append(str(family))

    def define_schema(cls: type[io.ComfyNode]) -> io.Schema:
        return io.Schema(
            node_id=node_id,
            display_name=str(model.get("displayName") or model["model"]),
            category=category_for(model),
            description=description,
            inputs=[_input_for(spec) for spec in specs],
            outputs=_outputs_for(plan),
            hidden=[io.Hidden.unique_id],
            search_aliases=aliases,
        )

    async def execute(cls: type[io.ComfyNode], **kwargs: Any) -> io.NodeOutput:
        unique_id = cls.hidden.unique_id if cls.hidden is not None else None
        outputs = await run_model(model, specs, plan, unique_id, kwargs)
        return io.NodeOutput(*outputs)

    return type(
        node_id,
        (io.ComfyNode,),
        {"define_schema": classmethod(define_schema), "execute": classmethod(execute)},
    )


# -- LoRA ------------------------------------------------------------------


class SpicyLoRA(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="SpicyAPI_LoRA",
            display_name="SpicyAPI LoRA",
            category="SpicyAPI/Utils",
            description=(
                "Adds a LoRA weights file to a SpicyAPI LoRA model. Chain several of these to "
                "stack LoRAs; each model accepts up to three."
            ),
            inputs=[
                io.String.Input(
                    "url",
                    default="",
                    placeholder="https://.../weights.safetensors",
                    tooltip="Direct download URL of a .safetensors LoRA file. It must open without a login.",
                ),
                io.Float.Input(
                    "scale",
                    default=1.0,
                    min=0.0,
                    max=4.0,
                    step=0.05,
                    round=0.01,
                    tooltip="Strength of this LoRA, 0 to 4.",
                ),
                LORAS.Input("loras", optional=True, tooltip="LoRAs from another SpicyAPI LoRA node."),
            ],
            outputs=[LORAS.Output("loras", display_name="loras")],
            search_aliases=["lora", "spicyapi lora"],
        )

    @classmethod
    def execute(cls, url: str, scale: float, loras: list[dict[str, Any]] | None = None) -> io.NodeOutput:
        stack = list(loras or [])
        link = url.strip()
        if link:
            if not link.startswith(("http://", "https://")):
                raise ValueError("The LoRA url must be an http(s) link to the weights file.")
            stack.append({"path": link, "scale": round(float(scale), 4)})
        return io.NodeOutput(stack)


# -- Chat ------------------------------------------------------------------


def make_chat_node(chat_models: list[dict[str, Any]]) -> type[io.ComfyNode] | None:
    ids = [str(m["model"]) for m in chat_models]
    if not ids:
        return None
    preferred = next((i for i in CHAT_DEFAULTS if i in ids), ids[0])

    class SpicyChat(io.ComfyNode):
        @classmethod
        def define_schema(cls) -> io.Schema:
            return io.Schema(
                node_id="SpicyAPI_Chat",
                display_name="SpicyAPI Chat",
                category="SpicyAPI/Text",
                description=(
                    "Ask a SpicyAPI text model and get its answer as a string, for example to "
                    "write or refine a prompt. Images are sent along when the model reads images; "
                    "a model that does not is refused with nothing charged."
                ),
                inputs=[
                    io.Combo.Input("model", options=ids, default=preferred),
                    io.String.Input("prompt", multiline=True, default=""),
                    io.String.Input(
                        "system", multiline=True, default="", tooltip="Optional instructions for the model."
                    ),
                    io.Autogrow.Input(
                        "images",
                        template=io.Autogrow.TemplatePrefix(
                            io.Image.Input("image", optional=True), prefix="image_", min=0, max=8
                        ),
                        optional=True,
                    ),
                    io.Int.Input(
                        "max_tokens",
                        default=2048,
                        min=1,
                        max=200000,
                        tooltip="Upper bound on the answer length, reasoning included.",
                    ),
                    io.Float.Input(
                        "temperature",
                        default=-1.0,
                        min=-1.0,
                        max=2.0,
                        step=0.05,
                        round=0.01,
                        tooltip="-1 leaves it to the model.",
                        advanced=True,
                    ),
                    io.Int.Input(
                        "seed",
                        default=0,
                        min=0,
                        max=2**31 - 1,
                        control_after_generate=io.ControlAfterGenerate.fixed,
                        tooltip="Changing it asks again; the model may still answer the same way.",
                        advanced=True,
                    ),
                ],
                outputs=[io.String.Output("text", display_name="text")],
                hidden=[io.Hidden.unique_id],
                search_aliases=["llm", "gpt", "prompt", "spicyapi chat"],
            )

        @classmethod
        async def execute(
            cls,
            model: str,
            prompt: str,
            system: str,
            max_tokens: int,
            temperature: float,
            seed: int,
            images: Any = None,
        ) -> io.NodeOutput:
            api_key, _ = config.get_api_key()
            if not api_key:
                raise ValueError(NO_KEY_MESSAGE)
            if not prompt.strip():
                raise ValueError("The prompt is empty.")
            node_id = cls.hidden.unique_id if cls.hidden is not None else None
            async with SpicyClient(api_key, config.get_base_url(), interrupt_check=_interrupt_check) as client:
                content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
                pictures = [p for v in _socket_values(images) for p in media.split_image_batch(v)]
                for picture in pictures:
                    uri = await _upload(client, *media.encode_image(picture))
                    content.append({"type": "image_url", "image_url": {"url": uri}})
                messages: list[dict[str, Any]] = []
                if system.strip():
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": content if pictures else prompt})
                body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
                if temperature >= 0:
                    body["temperature"] = temperature
                _show(node_id, "Asking...")
                answer = await client.chat_completion(body, idempotency_key=str(uuid.uuid4()))
            choices = answer.get("choices") or []
            message = (choices[0] or {}).get("message") if choices else None
            text = (message or {}).get("content") if isinstance(message, dict) else None
            if isinstance(text, list):
                text = "".join(str(part.get("text") or "") for part in text if isinstance(part, dict))
            if not isinstance(text, str):
                raise ValueError("The model returned no text.")
            usage = answer.get("usage") or {}
            _show(node_id, f"{usage.get('total_tokens', '?')} tokens")
            return io.NodeOutput(text)

    return SpicyChat


def _badge_labels(specs: list[FieldSpec], badge: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Dropdown label -> pricing value, for the pricing fields whose labels differ from values."""
    fields = set(badge.get("fields") or [])
    labels: dict[str, dict[str, str]] = {}
    for spec in specs:
        if spec.kind != "combo" or spec.name not in fields:
            continue
        mapping = {
            label: (str(value).lower() if isinstance(value, bool) else str(value))
            for label, value in zip(spec.options, spec.option_values, strict=True)
            if value is not OMIT
        }
        if any(label != value for label, value in mapping.items()):
            labels[spec.name] = mapping
    return labels


def build_all(items: list[dict[str, Any]]) -> tuple[list[type[io.ComfyNode]], dict[str, Any]]:
    """Node classes for the catalogue, plus the price badge table keyed by node type."""
    nodes: list[type[io.ComfyNode]] = [SpicyLoRA]
    badges: dict[str, Any] = {}
    seen: set[str] = set()
    chat_models: list[dict[str, Any]] = []
    for model in items:
        if model.get("enabled") is False:
            continue
        if model.get("modality") == "text" or "chat" in (model.get("tasks") or []):
            chat_models.append(model)
            continue
        node_id = node_id_for(str(model["model"]))
        if node_id in seen:
            log.warning("SpicyAPI: two models map to node type %s; keeping the first", node_id)
            continue
        try:
            node = make_model_node(model)
            node.GET_SCHEMA()  # fail here, per model, rather than taking the whole extension down
        except Exception as error:
            log.warning("SpicyAPI: skipped %s: %s", model.get("model"), error)
            continue
        seen.add(node_id)
        nodes.append(node)
        badge = badge_for(model)
        if badge:
            badge["labels"] = _badge_labels(build_fields(model.get("inputSchema") or {}), badge)
            badges[node_id] = badge
    chat = make_chat_node(chat_models)
    if chat is not None:
        nodes.append(chat)
    return nodes, badges
