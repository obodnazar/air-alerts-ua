"""Which raions are occupied: share of each raion's area inside the DeepState occupied polygon.

Replaces the hand-written list of occupied raions (post-hoc, 12 Oct).
Sources:
  - occupied territory, daily history: github.com/cyterat/deepstate-map-data (GPL-3.0, from deepstatemap.live)
  - 2020 raion boundaries with Ukrainian names: github.com/OPORA/map_2020 (rayon-ukraine-s-v5.geojson)
The map is taken as of AS_OF (end of the training period), so the rule is fixed before the test window.

    PYTHONPATH=src python -m airalerts.geo
"""

from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path

import pandas as pd

from .data import RENAMES
from .parse_signal import norm

AS_OF = "2026-08-31"
THRESHOLD = 0.5  # a raion is treated as occupied if more than half of its area is
GEO = Path("data/raw/geo")
OUT = Path("data/snapshot/occupied_share.csv")
URLS = {
    "deepstate.geojson.gz": "https://raw.githubusercontent.com/cyterat/deepstate-map-data/main/deepstate-map-data.geojson.gz",
    "rayon-ukraine-s-v5.geojson": "https://raw.githubusercontent.com/OPORA/map_2020/master/rayon-ukraine-s-v5.geojson",
}


def download() -> None:
    GEO.mkdir(parents=True, exist_ok=True)
    for name, url in URLS.items():
        if not (GEO / name).exists():
            urllib.request.urlretrieve(url, GEO / name)


def occupied_share() -> pd.DataFrame:
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.validation import make_valid

    ds = json.load(gzip.open(GEO / "deepstate.geojson.gz"))
    dated = [f for f in ds["features"] if f["properties"].get("date") and f["properties"]["date"] <= AS_OF]
    feat = max(dated, key=lambda f: f["properties"]["date"])
    occ = make_valid(shape(feat["geometry"]))
    occ = unary_union([occ])
    rows = []
    for f in json.load(open(GEO / "rayon-ukraine-s-v5.geojson", encoding="utf-8"))["features"]:
        name = norm(f["properties"]["rayon"])
        name = RENAMES.get(name, name)
        g = make_valid(shape(f["geometry"]))
        rows.append(dict(unit=name, occupied_share=g.intersection(occ).area / g.area, map_date=feat["properties"]["date"]))
    return pd.DataFrame(rows).sort_values("occupied_share", ascending=False)


def main() -> None:
    download()
    df = occupied_share()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"map date {df.map_date.iloc[0]}; raions over {THRESHOLD:.0%} occupied:")
    print(df[df.occupied_share > 0.05].round(3).to_string(index=False))


if __name__ == "__main__":
    main()


KYIV_NEIGHBOURS = ["Бучанський район", "Вишгородський район", "Броварський район", "Бориспільський район",
                   "Обухівський район", "Фастівський район"]  # Kyiv city is not in the raion layer


def neighbours(out: Path = Path("data/snapshot/raion_neighbours.json")) -> dict:
    """Raions whose boundaries touch (small tolerance for simplified polygons)."""
    from shapely.geometry import shape
    from shapely.validation import make_valid

    feats = json.load(open(GEO / "rayon-ukraine-s-v5.geojson", encoding="utf-8"))["features"]
    names = [RENAMES.get(norm(f["properties"]["rayon"]), norm(f["properties"]["rayon"])) for f in feats]
    geoms = [make_valid(shape(f["geometry"])).buffer(0.01) for f in feats]
    nb = {n: sorted(names[j] for j in range(len(names)) if j != i and g.intersects(geoms[j])) for i, (n, g) in enumerate(zip(names, geoms))}
    nb["Київ"] = KYIV_NEIGHBOURS
    for r in KYIV_NEIGHBOURS:
        nb[r] = sorted(set(nb.get(r, [])) | {"Київ"})
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=0), encoding="utf-8")
    return nb
