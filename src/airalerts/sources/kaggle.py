"""Download the Air Force daily reports dataset (Kaggle: piterfm/massive-missile-attacks-on-ukraine).

The public download endpoint works without a Kaggle account (checked 12 Oct 2026).

    python -m airalerts.sources.kaggle
"""

from __future__ import annotations

import argparse
import io
import urllib.request
import zipfile
from pathlib import Path

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

URL = "https://www.kaggle.com/api/v1/datasets/download/piterfm/massive-missile-attacks-on-ukraine"
WANT = "missile_attacks_daily.csv"


def download(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.endswith(WANT))
        dest = out_dir / WANT
        dest.write_bytes(z.read(name))
    print(f"{dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("data/raw/kaggle"))
    download(ap.parse_args().out)


if __name__ == "__main__":
    main()
