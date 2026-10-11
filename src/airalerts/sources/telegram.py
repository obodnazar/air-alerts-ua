"""Scrape the public web preview of a Telegram channel (t.me/s/<channel>).

No login and no API keys. The preview returns ~20 posts per page; older pages
are reached with ?before=<post_id>. Every post carries its publish time in UTC,
which is what makes leakage checks possible later: a feature built from a post
is available at `published_at`, never earlier.

Output is append-only JSON Lines, one post per line. The scraper is resumable:
rerunning it continues below the oldest id already saved.

    python -m airalerts.sources.telegram UkraineAlarmSignal --since 2026-08-15
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:  # use the OS certificate store (needed behind TLS-inspecting proxies)
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

USER_AGENT = "Mozilla/5.0 (research scraper; KSE student project)"
POST_SPLIT = '<div class="tgme_widget_message_wrap'


def _clean(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def fetch_page(channel: str, before: int | None, timeout: int = 30) -> str:
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_page(page: str, channel: str) -> list[dict]:
    posts = []
    for chunk in page.split(POST_SPLIT)[1:]:
        post_id = re.search(r'data-post="[^/"]+/(\d+)"', chunk)
        published = re.search(r'<time datetime="([^"]+)"', chunk)
        if not (post_id and published):
            continue
        body = re.search(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S
        )
        posts.append(
            {
                "channel": channel,
                "id": int(post_id.group(1)),
                "published_at": published.group(1),
                # Edited posts show only their latest text; keep the flag so they
                # can be excluded from features (the edit time is not exposed).
                "edited": "tgme_widget_message_edited" in chunk or ">edited<" in chunk,
                "text": _clean(body.group(1)) if body else "",
            }
        )
    return posts


def oldest_saved_id(path: Path) -> int | None:
    if not path.exists():
        return None
    ids = [json.loads(line)["id"] for line in path.open(encoding="utf-8") if line.strip()]
    return min(ids) if ids else None


def newest_saved_id(path: Path) -> int | None:
    if not path.exists():
        return None
    ids = [json.loads(line)["id"] for line in path.open(encoding="utf-8") if line.strip()]
    return max(ids) if ids else None


def update(channel: str, out: Path, delay: float = 1.2) -> None:
    """Fetch posts newer than the newest saved one (walks back from the live page)."""
    newest = newest_saved_id(out) or 0
    before, fresh = None, []
    while True:
        posts = [p for p in parse_page(fetch_page(channel, before), channel) if p["id"] > newest]
        if before is not None:
            posts = [p for p in posts if p["id"] < before]
        if not posts:
            break
        fresh += posts
        before = min(p["id"] for p in posts)
        time.sleep(delay)
    with out.open("a", encoding="utf-8") as fh:
        for p in sorted(fresh, key=lambda p: p["id"]):
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"update: {len(fresh)} new posts after id {newest}", flush=True)


def scrape(channel: str, since: str, out: Path, delay: float = 1.2, max_pages: int = 10_000) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    before = oldest_saved_id(out)
    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    pages = saved = failures = 0
    backoff = delay
    with out.open("a", encoding="utf-8") as fh:
        while pages < max_pages:
            try:
                page = fetch_page(channel, before)
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    backoff = min(backoff * 2, 120)
                    print(f"429, sleeping {backoff:.0f}s", file=sys.stderr)
                    time.sleep(backoff)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as err:
                failures += 1
                if failures > 5:
                    raise
                print(f"network error {err}, retry {failures}/5", file=sys.stderr)
                time.sleep(10)
                continue
            backoff, failures = delay, 0
            posts = [p for p in parse_page(page, channel) if before is None or p["id"] < before]
            if not posts:
                print("no older posts; stopping", file=sys.stderr)
                break
            for p in sorted(posts, key=lambda p: p["id"], reverse=True):
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            fh.flush()
            saved += len(posts)
            pages += 1
            before = min(p["id"] for p in posts)
            oldest = min(datetime.fromisoformat(p["published_at"]) for p in posts)
            if pages % 50 == 0:
                print(f"pages={pages} saved={saved} oldest={oldest.isoformat()}", flush=True)
            if oldest < since_dt:
                break
            time.sleep(delay)
    print(f"done: pages={pages} saved={saved} before={before}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("channel")
    ap.add_argument("--since", help="stop once posts are older than this UTC date")
    ap.add_argument("--update", action="store_true", help="fetch only posts newer than the newest saved")
    ap.add_argument("--out", type=Path, help="default: data/raw/telegram/<channel>.jsonl")
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--max-pages", type=int, default=10_000)
    args = ap.parse_args()
    out = args.out or Path("data/raw/telegram") / f"{args.channel}.jsonl"
    if args.update:
        update(args.channel, out, args.delay)
    else:
        if not args.since:
            ap.error("--since is required unless --update")
        scrape(args.channel, args.since, out, args.delay, args.max_pages)


if __name__ == "__main__":
    main()
