"""Regenerate the model lists in README.md and MODELS.md from data/catalog-snapshot.json.

The counts and names in the README come from here and nowhere else, so they cannot drift from
the nodes the package actually ships. Run after update_snapshot.py:

    python scripts/render_docs.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spicyapi_nodes.fields import model_page, node_id_for  # noqa: E402
from spicyapi_nodes.pricing import badge_for, format_usd  # noqa: E402

MODALITIES = (("video", "Video"), ("image", "Image"), ("audio", "Audio"))


def load() -> list[dict]:
    return json.loads((ROOT / "data" / "catalog-snapshot.json").read_text())["items"]


def media_models(items: list[dict]) -> list[dict]:
    return [
        m
        for m in items
        if m.get("enabled") is not False and m.get("modality") != "text" and "chat" not in (m.get("tasks") or [])
    ]


def chat_models(items: list[dict]) -> list[dict]:
    return [
        m
        for m in items
        if m.get("enabled") is not False and (m.get("modality") == "text" or "chat" in (m.get("tasks") or []))
    ]


def provider_name(model: dict) -> str:
    provider = str(model.get("provider") or "Other")
    return "SpicyAPI tools" if provider.lower() == "spicyapi" else provider


def families_by_provider(models: list[dict]) -> OrderedDict[str, OrderedDict[str, str]]:
    grouped: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()
    for model in models:
        family = str(model.get("familyDisplayName") or model.get("displayName") or model["model"])
        grouped.setdefault(provider_name(model), OrderedDict()).setdefault(family, model_page(model))
    return grouped


def stats(items: list[dict]) -> dict[str, int]:
    media = media_models(items)
    counts = {key: sum(1 for m in media if m.get("modality") == key) for key, _ in MODALITIES}
    return {
        "nodes": len(media),
        "families": len({m.get("familyDisplayName") for m in media}),
        "chat": len(chat_models(items)),
        **counts,
    }


def readme_block(items: list[dict]) -> str:
    media = media_models(items)
    lines: list[str] = []
    for key, title in MODALITIES:
        subset = [m for m in media if m.get("modality") == key]
        if not subset:
            continue
        lines.append(f"### {title} ({len(subset)} nodes)")
        lines.append("")
        lines.append("| Maker | Models |")
        lines.append("| --- | --- |")
        for provider, families in families_by_provider(subset).items():
            links = ", ".join(f"[{name}]({url})" for name, url in families.items())
            lines.append(f"| {provider} | {links} |")
        lines.append("")
    chats = chat_models(items)
    if chats:
        lines.append(f"### Text ({len(chats)} models in the SpicyAPI Chat node)")
        lines.append("")
        names = list(OrderedDict.fromkeys(str(m.get("familyDisplayName") or m["model"]) for m in chats))
        lines.append(", ".join(names) + ".")
        lines.append("")
    lines.append("Every node with its model ID and starting price: [MODELS.md](MODELS.md).")
    return "\n".join(lines)


def models_md(items: list[dict]) -> str:
    s = stats(items)
    out = [
        "# Models available as ComfyUI nodes",
        "",
        f"{s['nodes']} SpicyAPI models, one ComfyUI node each: {s['video']} video, {s['image']} image and "
        f"{s['audio']} audio. Text models ({s['chat']}) are picked inside the **SpicyAPI Chat** node.",
        "",
        "The list is generated from the model catalogue bundled with this release. ComfyUI loads the live "
        "catalogue at startup once an API key is set, so models added since then appear after a restart.",
        "Prices are starting prices; the badge on each node shows the rate for the options you pick.",
        "",
    ]
    for key, title in MODALITIES:
        subset = [m for m in media_models(items) if m.get("modality") == key]
        if not subset:
            continue
        out += [f"## {title}", "", "| Node | Model ID | From | Node type |", "| --- | --- | --- | --- |"]
        for model in sorted(subset, key=lambda m: (provider_name(m), str(m.get("displayName")))):
            badge = badge_for(model)
            price = f"{format_usd(badge['from'])} / {badge['unit']}" if badge else ""
            name = str(model.get("displayName") or model["model"]).replace("|", "/")
            out.append(
                f"| [{name}]({model_page(model)}) | `{model['model']}` | {price} | `{node_id_for(model['model'])}` |"
            )
        out.append("")
    return "\n".join(out)


def replace_between(text: str, marker: str, body: str) -> str:
    pattern = re.compile(rf"(<!-- {marker}:start -->\n).*?(<!-- {marker}:end -->)", re.S)
    if not pattern.search(text):
        raise SystemExit(f"README.md has no {marker} markers")
    return pattern.sub(lambda m: m.group(1) + body.rstrip("\n") + "\n" + m.group(2), text)


def main() -> None:
    items = load()
    s = stats(items)
    readme = (ROOT / "README.md").read_text()
    readme = replace_between(readme, "models", readme_block(items))
    summary = (
        f"**{s['nodes']} model nodes** ({s['video']} video, {s['image']} image, {s['audio']} audio) "
        f"from {s['families']} model families, plus {s['chat']} text models in one Chat node."
    )
    readme = replace_between(readme, "stats", summary)
    (ROOT / "README.md").write_text(readme)
    (ROOT / "MODELS.md").write_text(models_md(items))
    print(summary)


if __name__ == "__main__":
    main()
