"""Raions the bot serves, grouped by oblast, and search by town / hromada name.

`build()` writes data/snapshot/bot_catalog.json once (needs the data and shapely); the bot only reads it.
Town search: each hromada point (OPORA/map_2020) is placed inside a raion polygon, so typing
"Біла Церква" finds "Білоцерківська територіальна громада" → Білоцерківський район.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

CATALOG = Path("data/snapshot/bot_catalog.json")
APOS = str.maketrans({"ʼ": "'", "’": "'", "`": "'"})


def norm(s: str) -> str:
    s = s.lower().translate(APOS)
    s = re.sub(r"\b(територіальна громада|міська|селищна|сільська|громада|район|м\.)\b", " ", s)
    return re.sub(r"[^a-zа-яіїєґ']+", "", s)


def build() -> dict:
    from pathlib import Path as P

    from shapely.geometry import Point, shape
    from shapely.validation import make_valid

    from ..data import RENAMES, build_store, eligible_units
    from ..parse_signal import norm as unorm

    store = build_store(P("data/raw/vadimkin/official_data_uk.csv"), P("data/interim/telegram_episodes.parquet"))
    units = eligible_units(store)
    oblasts: dict[str, list[str]] = {}
    for u in units:
        oblasts.setdefault(store.oblast_of[u], []).append(u)
    rayons = json.load(open("data/raw/geo/rayon-ukraine-s-v5.geojson", encoding="utf-8"))["features"]
    polys = [(RENAMES.get(unorm(f["properties"]["rayon"]), unorm(f["properties"]["rayon"])), make_valid(shape(f["geometry"])))
             for f in rayons]
    towns = {}
    for f in json.load(open("data/raw/geo/hromady-titles.geojson", encoding="utf-8"))["features"]:
        name = f["properties"]["hromada"]
        pt = Point(f["geometry"]["coordinates"])
        hit = next((r for r, g in polys if g.contains(pt)), None)
        if "Київська міська" in name or name in ("Київ", "м. Київ"):
            hit = "Київ"
        towns[name] = hit
    cat = dict(oblasts={o: sorted(us, key=lambda x: (x != "Київ", x)) for o, us in sorted(oblasts.items())},
               units=sorted(units), towns=towns)
    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=0), encoding="utf-8")
    return cat


def load() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def search(query: str, cat: dict, n: int = 6) -> list[tuple[str, str | None]]:
    """[(label, unit or None if not served)]: raion names first, then hromadas."""
    q = norm(query)
    if len(q) < 3:
        return []
    units = cat["units"]
    hits = [(u, u) for u in units if norm(u).startswith(q[:5]) or q in norm(u)]
    scored = []
    for t, u in cat["towns"].items():  # several hromadas can share a stem (міська / сільська): score each
        k = norm(t)
        r = difflib.SequenceMatcher(None, q, k).ratio() + (0.1 if k[:4] == q[:4] else 0) + (0.08 if "міська" in t else 0)
        if r >= 0.62:
            scored.append((r, t, u))
    for _, t, u in sorted(scored, reverse=True):
        served = u if u in units else None
        if served and any(h[1] == served for h in hits):
            continue
        label = f"{t.replace(' територіальна громада', '')} → {served or 'район не обслуговується'}"
        hits.append((label, served))
    return hits[:n]


if __name__ == "__main__":
    c = build()
    print(len(c["units"]), "units,", len(c["oblasts"]), "oblasts,", len(c["towns"]), "hromadas,",
          sum(v is None for v in c["towns"].values()), "hromadas without a raion")
