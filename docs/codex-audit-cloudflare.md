# Codex audit, round 2: Cloudflare Worker + forecast publishing

You are auditing the repo `air-alerts-ua`. **Do not change code.** Report findings only.

## Context
- A Telegram bot sends a school-morning forecast: the chance of an air-raid alert at 08:00 and the chance of an all-clear before the school's control time.
- The Python pipeline (`src/airalerts/`) was audited in round 1 (`docs/codex-audit-prompt.md`).
- The bot now runs on **Cloudflare Workers (JavaScript) + D1**, deployed under `deploy/cloudflare/worker/`.
- **GitHub Actions** (`.github/workflows/forecast.yml` → `src/airalerts/botapp/publish.py`) computes the forecasts and POSTs them to the Worker's `/ingest`.
- The Python bot in `src/airalerts/botapp/` is the reference behaviour for the JS port.

## What to check

### 1. Security
- **Webhook check:** `/telegram` accepts a request only when it carries the right `X-Telegram-Bot-Api-Secret-Token`.
- **Bearer checks:** `/ingest` and `/setup` accept only the bearer `INGEST_SECRET`, and the comparison is constant-time. Look for bypasses: an empty secret, a missing header, the wrong method.
- **No leaks:** the token, the secrets and chat ids never appear in responses, errors, logs or the repo. Grep the whole history for `[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}`.
- **SQL:** all D1 queries are parameterised and none is built from user input.
- **Callback data:** forged data (`rm:0`, `cp:²`, `u:99999`, overlong strings) is rejected exactly as in Python (`router.py`, the `ALLOWED` whitelist).
- **Rate limits:** the global and per-chat limits exist in the JS port.
- **`.gitignore`:** `data/bot/` and `worker_secrets.env` are ignored, and no subscriber data is committed.

### 2. Port parity (JS vs Python)
- `ui.js` vs `ui.py`: texts, the `mood()` thresholds (0.2 and 0.5), advice thresholds per style, HTML escaping (`quote=False`), button styles (never `danger`).
- `router.js` vs `router.py`: every command and callback, `MAX_UNITS = 3`, pause and resume, `/stop`.
- `catalog.js` search vs `catalog.py`: city to raion mapping, the namesake bonus (Чернігів must not give Чернівецький; Буча must not give Бучацька), occupied raions excluded.
- List every behaviour that differs.

### 3. Scheduling and delivery (`src/index.js`)
- **`dueSlots`:**
  - converts to Kyiv time with DST; Ukraine switches to winter time on 25.10.2026, so test 24–26 Oct;
  - evening slots at 20:00, 20:30 and 21:00, morning slot at 08:05;
  - sends nothing on weekends or during a pause.
- **Delivery:**
  - idempotent: a cron firing twice, or two overlapping runs, must not send twice;
  - a failed send is retried and not marked as sent;
  - a 403 (the user blocked the bot) is handled.
- **Subrequest budget:** batches of 40, and the whole run must stay ≤ 50 subrequests on the Free plan. Count D1 calls and Telegram calls.
- **Stale data:**
  - no forecast for today means nothing is sent, not yesterday's forecast;
  - the evening forecast must be for tomorrow's school day; Friday evening → Monday? Check what actually happens.

### 4. Publishing (`publish.py`, `forecast.yml`)
- **No leakage:** the evening forecast uses only data up to 20:00, and the morning one only up to its issue time. Check the freshness guard (`last_refresh`).
- **Cron times:** the UTC crons in `forecast.yml` match the Kyiv times before and after 25 Oct.
- **Failures:** if the scrape fails, does the workflow fail loudly rather than publish old data?
- **Ingest payload:** it is validated: probabilities in [0,1], known units, the date format.

### 5. Tests
- Run `cd deploy/cloudflare/worker && npm test` (12 tests) and `PYTHONPATH=src pytest -q` (85 tests).
- List the important cases that have no test.

### 6. Docs
- `deploy/cloudflare/README.md` still describes a *Python* Worker and `pywrangler`, but the Worker is JS. List every claim that does not match the code.

## Report format
A table: **severity** (critical / high / medium / low) · **file:line** · **problem** · **concrete scenario** · **suggested fix**.

Then:
- the 3 most important fixes to do before today's 23:59 deadline;
- what you checked and found to be fine.

Mark anything you are unsure about as "unverified".
