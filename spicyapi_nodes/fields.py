"""Turn a model's published ``inputSchema`` into node inputs, and node values back into a request.

Pure Python on purpose: everything here is decided from the schema alone, so it can be tested
without ComfyUI, torch or a network.

Every model on SpicyAPI publishes a JSON Schema for its input, with presentation hints in a
per-property ``x-ui`` object (``widget``, ``order``, ``advanced``, ``step``...). The same schema
drives the web Playground, so mapping it faithfully is what keeps a node's options identical to
what the model accepts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
VIDEO_TYPES = ("video/mp4", "video/webm", "video/quicktime")
AUDIO_TYPES = ("audio/mpeg", "audio/wav")

# Combo entry meaning "leave this field out and let the model decide".
MODEL_DEFAULT = "model default"
# Numeric fields without a default use this as their "leave it out" value.
OMIT_NUMBER = -1

# ComfyUI caps one Autogrow group at 100 sockets; nobody wires more than this many by hand.
MAX_SOCKETS = 16

LORA_ITEM_KEYS = frozenset({"path", "scale"})


class Omit:
    """Sentinel: the field is not sent at all."""

    def __repr__(self) -> str:
        return "OMIT"


OMIT = Omit()


@dataclass
class FieldSpec:
    name: str
    kind: str
    """One of: image, images, video, videos, audio, audios, file, string, combo, int, float,
    bool, loras, json."""
    required: bool = False
    label: str = ""
    tooltip: str = ""
    advanced: bool = False
    order: float = 0
    default: Any = None
    multiline: bool = False
    options: list[str] = field(default_factory=list)
    option_values: list[Any] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    slider: bool = False
    seed: bool = False
    omit_when: Any = None
    """Widget value that means "leave the field out"."""
    min_items: int = 0
    max_items: int = 1
    accept: tuple[str, ...] = ()


def _humanize(name: str) -> str:
    """``reference_image_urls`` -> ``reference images``: labels follow the field, not the wire."""
    label = re.sub(r"_urls$", "s", name)
    label = re.sub(r"_url$", "", label)
    return label.replace("_", " ").strip() or name


def _media_types(prop: dict[str, Any]) -> tuple[str, ...]:
    source = prop.get("contentMediaType")
    if source is None and isinstance(prop.get("items"), dict):
        source = prop["items"].get("contentMediaType")
    if source is None:
        source = (prop.get("x-ui") or {}).get("accept")
    if isinstance(source, str):
        values = [part.strip() for part in source.split(",")]
    elif isinstance(source, list):
        values = [str(part) for part in source]
    else:
        values = []
    return tuple(value for value in values if value)


def _media_kind(types: tuple[str, ...], name: str) -> str:
    if any(t.startswith("image/") for t in types):
        return "image"
    if any(t.startswith("video/") for t in types):
        return "video"
    if any(t.startswith("audio/") for t in types):
        return "audio"
    if types:
        return "file"
    # No declared media type: fall back to the field name, which the platform keeps consistent.
    lowered = name.lower()
    for kind in ("image", "video", "audio"):
        if kind in lowered:
            return kind
    return "file"


def _type_of(prop: dict[str, Any]) -> str:
    value = prop.get("type")
    if isinstance(value, list):
        non_null = [v for v in value if v != "null"]
        return non_null[0] if len(non_null) == 1 else "mixed"
    return value if isinstance(value, str) else "mixed"


def _is_lora_list(prop: dict[str, Any]) -> bool:
    items = prop.get("items")
    if not isinstance(items, dict):
        return False
    keys = set((items.get("properties") or {}).keys())
    return "path" in keys and keys <= LORA_ITEM_KEYS


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _tooltip(prop: dict[str, Any], extra: str = "") -> str:
    text = str(prop.get("description") or "").strip()
    return f"{text} {extra}".strip() if extra else text


def build_fields(schema: dict[str, Any]) -> list[FieldSpec]:
    """Map every visible schema property to a node input, ordered as the Playground orders them."""
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    specs: list[FieldSpec] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        ui = prop.get("x-ui") if isinstance(prop.get("x-ui"), dict) else {}
        widget = ui.get("widget")
        if widget == "hidden":
            continue
        spec = _build_one(name, prop, ui, widget, name in required)
        if spec is not None:
            specs.append(spec)
    # x-ui.order is the only source of field order, highest first. Ties keep the schema order.
    specs.sort(key=lambda s: -s.order)
    return specs


def _build_one(name: str, prop: dict[str, Any], ui: dict[str, Any], widget: Any, required: bool) -> FieldSpec | None:
    base = FieldSpec(
        name=name,
        kind="string",
        required=required,
        label=str(ui.get("label") or _humanize(name)),
        tooltip=_tooltip(prop),
        advanced=bool(ui.get("advanced")),
        order=_number(ui.get("order")) or 0,
    )
    kind = _type_of(prop)

    if widget in ("upload", "multi-upload"):
        types = _media_types(prop)
        media = _media_kind(types, name)
        base.accept = types
        if widget == "upload" and kind == "string":
            base.kind = media
            return base
        if kind == "array":
            if media == "file":
                base.kind = "json"
                base.multiline = True
                base.default = ""
                return base
            base.kind = media + "s"
            base.min_items = int(prop.get("minItems") or (1 if required else 0))
            base.max_items = max(1, min(int(prop.get("maxItems") or MAX_SOCKETS), MAX_SOCKETS))
            return base

    if kind == "array" and _is_lora_list(prop):
        base.kind = "loras"
        base.max_items = int(prop.get("maxItems") or 8)
        return base

    if kind in ("array", "object", "mixed") or widget in ("json", "object-list", "chat-messages"):
        base.kind = "json"
        base.multiline = True
        default = prop.get("default")
        base.default = json.dumps(default) if default not in (None, [], {}) else ""
        shape = _item_shape(prop)
        base.tooltip = _tooltip(prop, f"JSON. {shape}" if shape else "JSON.")
        return base

    if "enum" in prop and isinstance(prop["enum"], list) and prop["enum"]:
        return _enum_field(base, prop, kind, ui)

    if kind == "boolean":
        if "default" in prop or required:
            base.kind = "bool"
            base.default = bool(prop.get("default", False))
        else:
            # A switch with no default has three states: on, off, and "not sent".
            base.kind = "combo"
            base.options = [MODEL_DEFAULT, "true", "false"]
            base.option_values = [OMIT, True, False]
            base.default = MODEL_DEFAULT
        return base

    if kind in ("integer", "number"):
        return _numeric_field(base, prop, kind, ui, name)

    # Plain strings.
    base.kind = "string"
    base.multiline = widget == "textarea"
    default = prop.get("default")
    base.default = default if isinstance(default, str) else ""
    if widget == "upload":
        # A document or other file field: a local path or a public URL.
        base.kind = "file"
    return base


def _item_shape(prop: dict[str, Any]) -> str:
    items = prop.get("items")
    if not isinstance(items, dict) or not isinstance(items.get("properties"), dict):
        return ""
    keys = list(items["properties"].keys())
    needed = set(items.get("required") or [])
    rendered = ", ".join(f'"{k}"' + ("" if k in needed else "?") for k in keys)
    return f"A list of objects with {rendered}."


def _enum_field(base: FieldSpec, prop: dict[str, Any], kind: str, ui: dict[str, Any]) -> FieldSpec:
    values = list(prop["enum"])
    default = prop.get("default")
    enum_labels = ui.get("enum_labels") if isinstance(ui.get("enum_labels"), dict) else {}
    special = any(str(v) in enum_labels and enum_labels[str(v)] != str(v) for v in values)
    if kind == "integer" and not special and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        ordered = sorted(values)
        contiguous = ordered == list(range(ordered[0], ordered[-1] + 1))
        if contiguous and len(ordered) > 4 and (default in values or base.required):
            # A long run of consecutive whole numbers (durations, mostly) reads better as a slider.
            base.kind = "int"
            base.minimum = ordered[0]
            base.maximum = ordered[-1]
            base.step = 1
            base.slider = ui.get("widget") == "slider"
            base.default = default if default in values else ordered[0]
            return base
    base.kind = "combo"
    labels = [str(enum_labels.get(str(v)) or (str(v).lower() if isinstance(v, bool) else v)) for v in values]
    if len(set(labels)) != len(labels):
        labels = [str(v).lower() if isinstance(v, bool) else str(v) for v in values]
    if default in values:
        base.options = labels
        base.option_values = values
        base.default = labels[values.index(default)]
    elif base.required:
        base.options = labels
        base.option_values = values
        base.default = labels[0]
    else:
        base.options = [MODEL_DEFAULT, *labels]
        base.option_values = [OMIT, *values]
        base.default = MODEL_DEFAULT
    return base


def _numeric_field(base: FieldSpec, prop: dict[str, Any], kind: str, ui: dict[str, Any], name: str) -> FieldSpec:
    integer = kind == "integer"
    base.kind = "int" if integer else "float"
    base.minimum = _number(prop.get("minimum"))
    base.maximum = _number(prop.get("maximum"))
    if base.minimum is None and _number(prop.get("exclusiveMinimum")) is not None:
        base.minimum = _number(prop["exclusiveMinimum"]) + (1 if integer else 0.001)
    if base.maximum is None and _number(prop.get("exclusiveMaximum")) is not None:
        base.maximum = _number(prop["exclusiveMaximum"]) - (1 if integer else 0.001)
    step = _number(ui.get("step"))
    if step is None:
        step = 1 if integer else _float_step(base.minimum, base.maximum)
    base.step = step
    base.slider = ui.get("widget") == "slider"
    default = _number(prop.get("default"))

    if name == "seed" and integer:
        # Always send a seed so a node gives the same result for the same settings; ComfyUI's
        # "control after generate" handles fresh seeds.
        base.seed = True
        base.tooltip = (
            "Same seed and settings, same result. Set 'control after generate' to randomize to "
            "get a new result on every run; every run is billed."
        )
        base.minimum = base.minimum if base.minimum is not None else 0
        base.maximum = base.maximum if base.maximum is not None else 2**31 - 1
        base.default = int(default) if default is not None else 0
        return base

    if default is not None:
        base.default = int(default) if integer else default
        return base
    if base.required:
        base.default = base.minimum if base.minimum is not None else 0
        base.default = int(base.default) if integer else float(base.default)
        return base
    if base.minimum is not None and base.minimum >= 0:
        # Optional and without a default: -1 means "not sent", so the model keeps its own default.
        base.omit_when = OMIT_NUMBER
        base.default = OMIT_NUMBER
        base.minimum = OMIT_NUMBER
        base.slider = False
        base.tooltip = (base.tooltip + " -1 leaves it to the model.").strip()
        return base
    # Unbounded below: there is no safe sentinel, so the value is only sent when wired in.
    base.kind = "int_socket" if integer else "float_socket"
    base.default = None
    return base


def _float_step(minimum: float | None, maximum: float | None) -> float:
    if minimum is not None and maximum is not None and maximum > minimum:
        span = maximum - minimum
        if span <= 2:
            return 0.01
        if span <= 20:
            return 0.1
        return 1.0
    return 0.01


def widget_to_value(spec: FieldSpec, value: Any) -> Any:
    """Convert one widget value to what the API expects, or OMIT to leave the field out.

    Media, LoRA and JSON fields are handled by the caller; this covers the plain widgets.
    """
    if value is None:
        return OMIT
    if spec.kind == "combo":
        text = str(value)
        if text in spec.options:
            chosen = spec.option_values[spec.options.index(text)]
            return chosen
        return OMIT if not spec.required else text
    if spec.kind in ("int", "int_socket"):
        number = int(value)
        if spec.omit_when is not None and number == spec.omit_when:
            return OMIT
        return number
    if spec.kind in ("float", "float_socket"):
        number = float(value)
        if spec.omit_when is not None and number == spec.omit_when:
            return OMIT
        return number
    if spec.kind == "bool":
        return bool(value)
    if spec.kind in ("string", "file"):
        text = str(value)
        if not text.strip() and not spec.required:
            return OMIT
        return text
    if spec.kind == "json":
        text = str(value).strip()
        if not text:
            return OMIT
        try:
            return json.loads(text)
        except ValueError as error:
            raise ValueError(f"'{spec.label}' must be valid JSON: {error}") from error
    return value


# -- Outputs ---------------------------------------------------------------


@dataclass
class OutputPlan:
    """What a model node hands to the next node."""

    primary: str
    """image, video, audio, text or previews."""
    last_frame: bool = False
    alpha: bool = False


def plan_outputs(model: dict[str, Any]) -> OutputPlan:
    modality = str(model.get("modality") or "")
    tasks = [str(t) for t in model.get("tasks") or []]
    schema = model.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    if "speech-to-text" in tasks:
        return OutputPlan("text")
    if "video-analyze" in tasks:
        return OutputPlan("previews")
    if modality == "video":
        return OutputPlan("video", last_frame="return_last_frame" in properties)
    if modality == "audio":
        return OutputPlan("audio")
    alpha = (
        "layer-decomposition" in tasks
        or "background" in properties
        or "background-remover" in str(model.get("model") or "")
    )
    return OutputPlan("image", alpha=alpha)


# -- Naming ----------------------------------------------------------------


def node_id_for(model_id: str) -> str:
    """Stable ComfyUI node type for a model. Workflows store it, so it must never change."""
    return "SpicyAPI_" + re.sub(r"[^0-9A-Za-z]+", "_", model_id).strip("_")


def category_for(model: dict[str, Any]) -> str:
    modality = str(model.get("modality") or "other").capitalize()
    provider = str(model.get("provider") or "").strip() or "Other"
    if provider.lower() == "spicyapi":
        provider = "Tools"
    return f"SpicyAPI/{modality}/{provider}"


def model_page(model: dict[str, Any]) -> str:
    """The model's page on spicyapi.ai: prices, examples and the full parameter reference."""
    slug = model.get("familyPageSlug")
    return f"https://spicyapi.ai/models/{slug}" if slug else "https://spicyapi.ai/models"
