# Codex audit, round 5: verify the round-4 fixes (free-tier limits)

**Do not change code.** Report findings only.

## What to verify
Round 4 (`docs/codex-audit-limits.md`) found these risks. They were fixed in commit `9d16c16` (see `git show 9d16c16` and `git diff 1a90fab..9d16c16`). For each fix, say whether it is **fixed**, **partly fixed** or **not fixed**, with proof: a test, `EXPLAIN QUERY PLAN`, a measured row count, or a reproduction.

| # | Round-4 finding | Claimed fix | Where |
|---|---|---|---|
| 1 | Full scans of `outbox` by token; `outbox` grows forever | indexes `outbox_token` and `outbox_day`; `Store.cleanup()` at 03:00 Kyiv drops outbox rows older than 3 days and school forecasts older than 14 days | `store.js` (`cleanup`, `claim`, `settle`, `finish`), `index.js` (`scheduled`), `schema.sql`, `migrations/0005_limits.sql` |
| 2 | The seed costs ~10–14 k writes per call, 6 calls a day | the seed is sent about twice a day (`publish.extras_due`); an identical seed is skipped by hash; the upsert writes only rows that change (`WHERE` on `DO UPDATE`); rows that are gone are deleted by key; no staging table | `store.js` `applySeed`, `publish.py` |
| 3 | The rate limiter writes ~6 D1 rows per tap | moved to isolate memory, no D1 | `store.js` `allowed`, `LIMITS` |
| 4 | Delivery after `/ingest` can be cut by `waitUntil` (~30 s) before `finish()` | a 20 s time budget per run; progress settled every 10 sends | `index.js` `deliver`, `store.js` `settle` |
| 5 | A seed larger than D1's 2 MB string limit passes validation | a UTF-8 size check of at most 1.5 MB | `index.js` `invalidAlerts` |
| 6 | Extras failures leave a green workflow; no quota or health signal | any failed POST fails the run; `/health` checks D1, scrape age and ingest age and returns 503 otherwise; the workflow curls it | `publish.py`, `index.js` `/health`, `forecast.yml` |
| 7 | The GitHub cache saved a ~240 MB copy on every run | saved only when `data/interim/last_refresh` exists | `forecast.yml` |

## Also check
1. **New bugs introduced by the fixes:**
   - **In-memory limiter:**
     - Is it per isolate, and is that acceptable?
     - Can memory grow without bound?
     - Does `Store` take `db.limits` only in tests?
   - **`settle()`:**
     - Can it mark a row that another run has claimed since?
     - Is the JSON path for negative chat ids correct?
   - **Seed hash:** can a hash match skip a seed that should apply? What if `since`/`data_end` change but the alerts do not?
   - **The upsert `WHERE`:**
     - Does it still close open alerts?
     - Does it still update levels only when `newer`?
     - Does it still raise `max_level`?
     - Compare its behaviour with the round-3 version on the existing tests and fixtures.
   - **`cleanup()`:**
     - Can it delete a row still needed? Think of a pending morning slot, a Monday forecast read on Sunday, or `/now`.
     - What happens on DST days?
     - What if the 03:00–03:05 cron tick is missed?
   - **`/health`:**
     - Can weekend gaps cause false 503s, since the hourly parameters must arrive daily?
     - Does an unauthenticated `/health` leak anything sensitive?
     - What does it cost in rows read per call?
   - **`extras_due`:**
     - Weekends.
     - Both DST offsets: with crons at 16:41/17:41/18:05 and 04:41/05:41/06:02 UTC, do the extras arrive at least once per window, and at most about twice a day?
     - A GitHub delay of 30+ minutes.
   - **The workflow `/health` step:** it runs `if: always()`. Will it fail runs during a temporary blip and send noisy e-mails? Is that acceptable?
2. **Re-run the daily budget** for scenarios a (5 subscribers), b (1 000) and c (10 000), the same as in round 4. Give reads and **writes** per day, and compare with round 4.
3. **Migration safety.**
   - Is `migrations/0005_limits.sql` (DROP `hits` and `seed_keys`, DELETE old outbox rows) safe to apply on production with the code already deployed?
   - Is `schema.sql` equal to the migrated production schema?
4. **Tests.**
   - Run `cd deploy/cloudflare/worker && npm test` and `PYTHONPATH=src pytest -q`, on a fresh clone too.
   - Do the new tests prove the claims, or could they pass with the old code? Try reverting each fix one at a time and check that a test fails.
5. **Production, read-only.** Run `npx wrangler d1 info shkilnyi-ranok`, `npx wrangler d1 insights shkilnyi-ranok --timePeriod 1d --sort-type sum --sort-by reads --json` and the same with `--sort-by writes`, and `curl https://shkilnyi-ranok.shkilnyi-ranok-worker.workers.dev/health`. Note: the 5 M read limit was hit on 13 Oct and resets at 00:00 UTC. Compare real numbers with your estimates. Do not run SQL that writes.

## Report format
- A table of the 7 fixes: **fixed / partly / not fixed** · **proof** · **what is left**.
- A table of new problems: **severity** · **file:line** · **problem** · **concrete scenario** · **fix**.
- The updated budget (reads and writes per day) for a, b and c, next to round 4.
- Verdict: will it run reliably every day on the free plans at the current scale? What is the next limit to hit, and at how many subscribers?
- What you could not verify.
