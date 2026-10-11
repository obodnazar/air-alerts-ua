# Air raid alerts in Ukraine: time series for real decisions

KSE Agentic AI School 2026, Stage 2 pet-project. **Status: work in progress (11 Oct 2026). Research is done; the problem is being chosen.**

The goal is a forecast that changes a real decision, evaluated on the latest data with no leakage: nothing used for training or as an input comes from after the moment the forecast is made.

## Where things are

| Path | What |
|---|---|
| [docs/problems-plain.md](docs/problems-plain.md) | five candidate problems in plain language (UA) |
| [docs/problem-options.md](docs/problem-options.md) | the same five with architectures, evaluation design, risks (UA) |
| [docs/research/](docs/research/) | research reports: data, competitors, people's pains, economy, military data, Telegram lead time, products and regulation (UA) |
| `src/airalerts/sources/` | data collection |
| `data/` | local only, git-ignored; everything is re-downloadable |

## Data, as verified on 11 Oct 2026

| Source | Coverage | Note |
|---|---|---|
| [Vadimkin dataset](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset), official feed | 15 Mar 2022 → **7 Sep 2026** | frozen: its source channel stopped posting on 7 Sep 2026 |
| Vadimkin dataset, volunteer feed | 25 Feb 2022 → today | oblast level only |
| Telegram [@UkraineAlarmSignal](https://t.me/s/UkraineAlarmSignal) | live | raion/hromada level, yellow/red alert level, threat type; scraped here to fill 7 Sep → today |
| Kaggle [Massive Missile Attacks on Ukraine](https://www.kaggle.com/datasets/piterfm/massive-missile-attacks-on-ukraine) | 2022 → 10 Oct 2026 | daily Air Force reports; usable only from their publication time |

The series has at least five structural breaks: raion-level alerts (Dec 2025), jet-drone "streaks" (mid-Aug 2026), restricted Air Force reporting (11–12 Aug 2026), yellow/red alert levels (4–6 Sep 2026), voice-only all-clear (18 Sep 2026). See [docs/research/01-data-landscape.md](docs/research/01-data-landscape.md).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m airalerts.sources.vadimkin
PYTHONPATH=src .venv/bin/python -m airalerts.sources.telegram UkraineAlarmSignal --since 2026-08-15
```

The Telegram scraper reads the public web preview only (no login, no API keys), waits between requests and resumes where it stopped.

## Responsible use

Uses only the start and end times of public alerts and published daily reports. It does not show or use real-time movement of threats or air defence. It is a research project, not a replacement for official alert signals.
