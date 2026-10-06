"""Refresh data/catalog-snapshot.json from the live API.

The snapshot only matters before a user has set a key: once one is set, the node list is fetched
live at every ComfyUI start. Run it before a release:

    SPICY_API_KEY=sk-... python scripts/update_snapshot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spicyapi_nodes import catalog  # noqa: E402


async def main() -> None:
    api_key = os.environ.get("SPICY_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("set SPICY_API_KEY")
    items = await catalog.fetch_live(api_key)
    if not items:
        raise SystemExit("the API returned no models; refusing to write an empty snapshot")
    target = ROOT / "data" / "catalog-snapshot.json"
    with open(target, "w", encoding="utf-8") as handle:
        # ensure_ascii keeps the file pure ASCII whatever the model data contains.
        json.dump({"fetchedAt": int(time.time()), "items": items}, handle, ensure_ascii=True, indent=1)
        handle.write("\n")
    print(f"wrote {len(items)} models to {target.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
