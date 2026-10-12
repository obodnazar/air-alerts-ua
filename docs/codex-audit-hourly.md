# Codex audit, round 3: the whole project (branch `hourly-risk` = main + new features)

**Do not change code.** Report findings only.

## Scope: everything, end to end
Audit the whole repository, not only the diff.

**Part A · the core research.** `src/airalerts/` (data, parse_signal, dataset, models, evaluate, clearance, live), `tests/`, `PREREG.md`, `README.md`, `results/`, `scripts/reproduce.sh`.
- **Reproducibility:** run `scripts/reproduce.sh` from a fresh clone. Do the results match the committed ones?
- **Leakage:** check every feature at every issue time. Is the 1-SE feature selection done on validation only?
- **Labels:** censoring, sub-minute times, the source switch on 28 Aug, renamed and occupied raions.
- **Statistics:** is the paired block bootstrap correct? Was the test touched only once (check the git history against `PREREG.md`)?
- **Claims vs numbers:** does every number in the README match the files in `results/`? Are post-hoc results clearly separated from registered ones?
- **Live test of 12 Oct:** was the forecast committed before 08:00, and was it scored correctly?

**Part B · the bot and hosting (live on main).** The Python reference bot `src/airalerts/botapp/`, the Worker `deploy/cloudflare/worker/`, `.github/workflows/forecast.yml`, `deploy/`. Re-check every round-2 finding (`docs/codex-audit-cloudflare.md`). Also check the new early start with a wait in `publish.py`, and delivery right after `/ingest`.

**Part C · the new features (branch only, not deployed).** Run `git diff main...hourly-risk`. Key files:
- **Model:** `src/airalerts/hourly.py`
- **Leakage tests:** `tests/test_hourly.py`, `tests/test_leakage.py`
- **Publisher:** `src/airalerts/botapp/publish.py`
- **Results:** `PREREG.md` item 12, `results/hourly_gates.json`, `results/posthoc_hourly_*.csv`
- **Worker:** `deploy/cloudflare/worker/src/{signal,live,hourly,index,router,store,ui}.js`, `migrations/0003_live_alerts.sql`, `wrangler.toml`

## What to check
1. **Leakage.**
   - Every hourly feature and parameter must use only data published before the forecast time.
   - The backtest must be rolling and use only past days.
   - The truncation test must really cover every hour.
   - Try to plant a one-hour peek and check that a test catches it.
2. **Honest result.**
   - Does PREREG item 12 predate the run (git history)?
   - Do the README and PREREG numbers match `results/`?
   - Is the display rule enforced in code? It says to show the model only where it beats climatology, and the plain historical share elsewhere.
3. **Safety framing.**
   - During an active alert, no screen may suggest going out. It must say shelter and official alerts.
   - The texts must not present a probability as a warning.
4. **Worker scraping of t.me/s:**
   - **Parser parity:** compare `signal.js` with Python `parse_signal.py` on the fixtures, including multi-section posts, yellow/red levels and all-clears.
   - **Broken page:** an HTTP 200 maintenance page, an empty page, or a changed layout must not mark data fresh or close alerts.
   - **Resilience:** the scrape must be idempotent, survive overlapping crons, and leave no stale "alert on" state.
   - **Free plan limits:** CPU time; no more than 50 subrequests and 50 D1 queries per invocation, counting the scrape and delivery in the same run.
5. **Ingest and bot input:**
   - Strict validation of the new ingest kinds.
   - Callback whitelisting for `hr:` and `rc:`, plus forged values.
   - Rate limits and the new commands `/hours` and `/recent`.
6. **Regressions.** Everything fixed in round 2 must still hold (`docs/codex-audit-cloudflare.md`):
   - outbox budget and leases;
   - DST;
   - the 409 first-version rule;
   - no token in errors.
7. **Tests.**
   - Run `cd deploy/cloudflare/worker && npm test` and `PYTHONPATH=src pytest -q`.
   - List the important cases that have no test.

## Report format
A table: **severity** · **file:line** · **problem** · **concrete scenario** · **fix**.

Then:
- the 3 fixes needed before deploy;
- what you checked and found fine;
- what you could not verify.
