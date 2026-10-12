# Audit brief for Codex

Copy everything below the line into Codex (or any coding agent) with this repository checked out.

---

You are auditing the repository `air-alerts-ua`. It is a Python research project and a Telegram bot, built under time pressure with an AI assistant. **Find what is wrong. Do not praise.** Work read-only first; propose fixes as separate commits only after you report.

## What the project claims
1. **Forecasts.** For each of 104 Ukrainian raions it predicts:
   - whether an air raid alert is active at 08:00, issued at 20:00 the evening before and at 07:00;
   - whether an alert active at 08:00 ends by the school's "control point" (09–12).

   This supports a Ministry of Education rule for school mornings.
2. **Leakage.** Every feature uses only information published before the issue time.
3. **Evaluation.** It was pre-registered in `PREREG.md`. Changes after registration are listed there; post-hoc work is labelled.
4. **Results.** Headline numbers are in `README.md` (test 1 Sep – 9 Oct 2026; a live forecast on 12 Oct committed before the outcome).
5. **The bot.** `src/airalerts/botapp/` serves those forecasts; it is meant to run 24/7 on a small VM (`deploy/`).

## How to run
```bash
./scripts/reproduce.sh          # ~1.5 min: downloads data, uses data/snapshot/, runs tests and the evaluation
PYTHONPATH=src .venv/bin/python -m pytest -q
```

## Audit checklist (report each item: OK / problem + file:line + concrete failing input)

### A. Leakage and evaluation integrity (most important)
- `dataset.py`, `attacks.py`, `airforce.py`, `clearance.py`, `levels.py`, `cost.py`:
  - can any feature, label or fitted rate use data after `issue_time`?
  - check merged intervals in `data.Store`;
  - check `active_until` when an alert spans the issue time;
  - check the rolling windows (`hist` filters `< d`);
  - check that Kaggle reports are available only at `time_end + 2h`;
  - in `clearance.fit_predict`, the leave-one-day-out rates and the `remaining` filter.
- Is the truncation test in `tests/test_leakage.py` strong enough? Write a test that would fail if a 1-hour peek were introduced anywhere.
- `evaluate.py`:
  - was the test window really untouched by model choice, thresholds and feature selection?
  - does `decision_days` fit only on data before the evaluated window?
  - is the paired block bootstrap correct (blocks of days, paired)?
- Source stitching (`data.SWITCH` 28 Aug; official feed vs Telegram): can a gap or a duplicate double-count alerts around the switch?
- Time zones: all timestamps UTC inside, Kyiv for day boundaries. **Ukraine changes from EEST to EET on 25 Oct 2026.** Check `kyiv()`, the bot schedule (`botapp/main.py`), and anything that compares `"HH:MM"` strings.

### B. Data correctness
- `parse_signal.py`:
  - multi-unit posts with or without bullets;
  - level changes and downgrades;
  - posts edited later;
  - an all-clear without a start;
  - two starts without an all-clear;
  - the apostrophe variants.

  Find a real post format it mishandles (`data/raw/telegram/UkraineAlarmSignal*.jsonl` after scraping, or the episodes snapshot).
- `geo.py`: occupied-share threshold, name joins (renamed raions), the Kyiv special case.
- `botapp/catalog.py` search: wrong raion for a common town? Try the 50 largest Ukrainian cities.

### C. Security (bot and repo)
- **Secrets:**
  - token handling in `botapp/telegram.py`, `bot.py`, `deploy/`: can the token be printed in a log or traceback? Is it ever in a URL that gets logged?
  - scan the full git history for anything that looks like a token or key: `git log -p | grep -E "[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}"`.
- **Input handling:**
  - forged `callback_data`, very long messages, unicode tricks;
  - SQL in `botapp/store.py`: the f-string field name is whitelisted; is the whitelist bypassable?
  - rate limiting per chat: can it be bypassed with many chats?
- **Personal data:** `data/bot/` must be git-ignored; nothing that identifies a user may be logged.
- **Scraping:** `sources/telegram.py` and `banner.py` fetch t.me pages. Any SSRF or path issue? Is the HTML parsing robust to hostile content?
- **Dependencies:** unpinned versions in `requirements.txt`. Run `pip-audit` and report known CVEs.
- **Deploy:** `deploy/setup.sh` and the systemd unit: permissions on the env file, the user, hardening options, `curl | bash`-style risks.

### D. Bot behaviour
- **Scheduler (`due`, `due_slots`, `prepare`):**
  - start at 19:44 / 20:10 / 21:45 / 08:00 / 09:40;
  - Friday and weekend handling;
  - DST night (25 Oct);
  - two slots in one tick;
  - forecast preparation failing or taking longer than the slot;
  - restart during a broadcast (idempotency through the `sent` table: the key is `(chat, kind, day)`, so does a user who changes the evening slot after receiving still get exactly one message?).
- **Threads:** `prepare` runs in a thread with its own SQLite connection while the main thread writes. Any race, `database is locked`, or a lost write?
- **Telegram API:**
  - `editMessageText` on an old message;
  - "message is not modified";
  - 400 from a too-long message;
  - 409 when two instances poll.
- **UX:**
  - a user with 3 raions in different oblasts;
  - a user removes all raions;
  - pause until a date in the past;
  - `/now` before the evening cache exists.

### E. Code quality that can hide bugs
- Dead code, duplicated logic (`brief.py` vs `botapp/forecasts.py`), silent `except Exception`.
- Tests that pass for the wrong reason.

## Output format
A table with columns **severity** (critical / high / medium / low), **area** (A–E), **file:line**, **what is wrong**, **how to reproduce**, **suggested fix**. Then the top 5 fixes in order. Do not change results files or `PREREG.md`.
