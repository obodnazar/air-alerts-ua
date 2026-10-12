"""Download the Vadimkin air-raid sirens dataset (official and volunteer feeds).

As checked on 11 Oct 2026:
- official_data_uk.csv (oblast/raion/hromada levels) stops at 2026-09-07 05:28 UTC,
  because its source channel @air_alert_ua stopped posting that day;
- volunteer_data_en.csv (oblast level only) is updated daily.

    python -m airalerts.sources.vadimkin
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

try:  # use the OS certificate store (python.org builds on macOS ship without one)
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

BASE = "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/main/datasets/"
FILES = ["official_data_uk.csv", "volunteer_data_en.csv"]


def download(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = out_dir / name
        print(f"downloading {name} ...", flush=True)
        urllib.request.urlretrieve(BASE + name, dest)
        print(f"  {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("data/raw/vadimkin"))
    download(ap.parse_args().out)


if __name__ == "__main__":
    main()
