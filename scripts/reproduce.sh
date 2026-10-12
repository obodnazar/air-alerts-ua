#!/usr/bin/env bash
# Reproduce every number in the README from a fresh clone, in a few minutes.
# Uses the committed snapshot of parsed Telegram alerts (data/snapshot/, 15 Aug - 12 Oct 2026)
# instead of re-scraping ~3 000 pages. To rebuild the snapshot from scratch, see the README.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
[ -x "$PY" ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; PY=.venv/bin/python; }
export PYTHONPATH=src
$PY -m airalerts.sources.vadimkin
$PY -m airalerts.sources.kaggle
mkdir -p data/interim && cp data/snapshot/telegram_episodes.parquet data/interim/
cp results/summary.json /tmp/air-alerts-committed-summary.json
$PY -m pytest -q -p no:cacheprovider
$PY -m airalerts.evaluate --test
$PY -m airalerts.levels
$PY -m airalerts.cost
$PY -m airalerts.clearance
$PY -m airalerts.figures
$PY scripts/check_results.py /tmp/air-alerts-committed-summary.json
echo "done: results/ and figures/ regenerated"
