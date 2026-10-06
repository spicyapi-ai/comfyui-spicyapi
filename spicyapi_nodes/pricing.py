"""Price tables for the badge each model node shows.

The badge is a rate, never a total: "$0.432 / second", not "$4.32". A total depends on things the
node cannot know before it runs (how long an uploaded clip is, how many images come back), and
an estimate shown as a fact is worse than none. The authoritative amount is the quote taken when
the node runs, and the charged amount is printed on the node afterwards.

A variant key is built the way the platform builds it (``service/pricing_variant.go``): the pricing
fields sorted by name, each as ``field=`` plus the query-escaped value, joined with ``;``; booleans
are ``true``/``false``, and a field missing from the request takes its schema default. Fields in
``variantKeyOmitDefaults`` drop out while they hold their default, ``presenceFields`` are true when
the input they name has something connected, and when exactly one part is left and its field is
not omittable the key is the bare value (``720p`` rather than ``resolution=720p``).
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote_plus, unquote_plus

UNIT_LABELS = {
    "per_second": "second",
    "per_image": "image",
    "per_request": "run",
    "per_1k_characters": "1k characters",
    "per_1k_tokens": "1k tokens",
}


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def format_usd(value: Any) -> str:
    """$0.432, $0.0246, $1.08: enough digits to be exact, no trailing zeros."""
    amount = _decimal(value)
    if amount is None:
        return str(value)
    text = format(amount.normalize(), "f")
    if "." in text:
        whole, fraction = text.split(".", 1)
        if len(fraction) < 2:
            fraction = fraction.ljust(2, "0")
        text = f"{whole}.{fraction}"
    else:
        text = f"{text}.00"
    return f"${text}"


def badge_for(model: dict[str, Any]) -> dict[str, Any] | None:
    pricing = [p for p in model.get("pricing") or [] if isinstance(p, dict)]
    if not pricing:
        start = model.get("startingPrice")
        pricing = [start] if isinstance(start, dict) else []
    if not pricing:
        return None
    units = Counter(str(p.get("unit") or "") for p in pricing)
    unit = units.most_common(1)[0][0]
    table: dict[str, str] = {}
    lowest: Decimal | None = None
    for row in pricing:
        if str(row.get("unit") or "") != unit:
            continue
        amount = _decimal(row.get("price"))
        if amount is None:
            continue
        table[str(row.get("variant") or "")] = str(row.get("price"))
        if lowest is None or amount < lowest:
            lowest = amount
    if lowest is None:
        return None
    schema = model.get("inputSchema") or {}
    x_pricing = schema.get("x-pricing") or {}
    properties = schema.get("properties") or {}
    presence = {str(k): str(v) for k, v in (x_pricing.get("presenceFields") or {}).items()}
    fields = sorted(str(f) for f in x_pricing.get("variantFields") or [])
    defaults = {
        name: _key_value(properties[name]["default"])
        for name in fields
        if isinstance(properties.get(name), dict) and "default" in properties[name]
    }
    omit_defaults: dict[str, str] = {}
    for name in x_pricing.get("variantKeyOmitDefaults") or []:
        if name in presence:
            omit_defaults[name] = "false"
        elif name in defaults:
            omit_defaults[name] = defaults[name]
    return {
        "unit": UNIT_LABELS.get(unit, unit.replace("per_", "").replace("_", " ")),
        "fields": fields,
        "defaults": defaults,
        "omitDefaults": omit_defaults,
        "presence": presence,
        "table": table,
        "from": format(lowest.normalize(), "f"),
        "approximate": x_pricing.get("billingMode") == "formula",
    }


def _key_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def variant_key(badge: dict[str, Any], values: dict[str, Any], connected: set[str]) -> str:
    """The same key the JavaScript badge builds; kept here so the rule is tested in one place."""
    parts: list[tuple[str, str]] = []
    omit = badge.get("omitDefaults") or {}
    for name in sorted(badge.get("fields") or []):
        presence_target = (badge.get("presence") or {}).get(name)
        if presence_target is not None:
            value = _key_value(presence_target in connected)
        elif name in values:
            value = _key_value(values[name])
        elif name in (badge.get("defaults") or {}):
            value = badge["defaults"][name]
        else:
            continue
        if name in omit and omit[name] == value:
            continue
        parts.append((name, value))
    if len(parts) == 1 and parts[0][0] not in omit:
        return parts[0][1]
    return ";".join(f"{name}={quote_plus(value, safe='')}" for name, value in parts)


def parse_variant_key(badge: dict[str, Any], key: str) -> dict[str, str]:
    """Inverse of variant_key, for the tests: field -> value as it appears in the key."""
    if not key:
        return {}
    if "=" not in key and len(badge.get("fields") or []) >= 1:
        non_omittable = [f for f in badge["fields"] if f not in (badge.get("omitDefaults") or {})]
        if len(non_omittable) == 1:
            return {non_omittable[0]: key}
    return {name: unquote_plus(value) for name, value in (part.split("=", 1) for part in key.split(";"))}


def describe_price(badge: dict[str, Any] | None) -> str:
    if not badge:
        return ""
    prefix = "about " if badge.get("approximate") else ""
    if len(badge.get("table") or {}) > 1:
        return f"From {prefix}{format_usd(badge['from'])} per {badge['unit']}."
    return f"{prefix.capitalize()}{format_usd(badge['from'])} per {badge['unit']}.".strip()
