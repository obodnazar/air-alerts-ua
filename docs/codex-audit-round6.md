# Codex audit, round 6: verify round 5, then the whole project again

**Do not change code, `results/`, `PREREG.md` or production data.** Report findings only. Use a temporary clone for anything that writes.

Five audit rounds in two days changed a lot of code quickly. Part A checks the last fixes. Part B looks for what broke or was never thought through, across the whole project.

## Part A · the round-5 fixes (commit `6154e09`, `git diff 9d16c16..6154e09`)
For each fix, say whether it is **fixed**, **partly fixed** or **not fixed**. Give proof: a test, a reproduction or a measured number. Then **revert each fix in a temporary copy and check that a test fails**. Say which ones stay green.

| # | Claimed fix | Where |
|---|---|---|
| 1 | One delivery deadline per run (22 s) across all slots, and a 6 s timeout on each Telegram call (`AbortSignal.timeout`) | `index.js` (`newBudget`, `deliver`, `runDue`), `telegram.js` |
| 2 | Hourly parameters and alert history keyed by publishing window (`YYYY-MM-DD-am/pm`): the Worker applies the first success per window and skips the rest; a manual run sends `replace: true` | `publish.py` (`extras_window`), `index.js` (`/ingest`, `ingestExtras`) |
| 3 | The seed hash includes `max_post_id` | `store.js` (`applySeed`) |
| 4 | `/health` checks: database readable; live data by the bot's own rule (`liveState`, 15 min, no gap); school forecast ≤ 64 h; hourly ≤ 26 h. The workflow curls it with 3 retries | `index.js` (`/health`), `forecast.yml` |
| 5 | Limiter map capped at 2 000 chats, pruned before any decision | `store.js` (`allowed`) |
| 6 | Daily cleanup on the first tick at or after 03:00 Kyiv, once per day (`cleanup_day`) | `index.js` (`dailyCleanup`) |
| 7 | One GitHub cache copy per publishing window (`posts-YYYY-MM-DD-AM/PM`) | `forecast.yml` |

**Specific questions:**
- **Window key (2):**
  - Is the date or am/pm wrong at the edges? For example a run at 21:30, a delayed run after midnight, a morning backup at 09:30, or the DST days 25.10.2026 and 28.03.2027.
  - Can a failure after `ingestExtras` but before `kv(done)` cause a skipped or double apply?
  - Is `skipped` reported as success, so the run is green when it should be?
- **Deadline (1):**
  - What happens to rows claimed but not sent before the deadline?
  - Does a 429 or a timeout leave rows stuck until the lease expires?
  - Is `AbortSignal.timeout` available in the Workers runtime that `wrangler.toml` targets (`compatibility_date`)?
- **`/health` (4):**
  - Can it be green while the bot cannot deliver? Can it be red on a normal weekend or holiday?
  - Is 64 h right for Friday 08:00 → Sunday 20:00, and for a 3-day weekend?
  - What does each call cost in rows read?
- **Cache key (7):** `date +%p` depends on the locale. Is it `AM`/`PM` on `ubuntu-latest`? Is a second save with the same key a warning, or does it fail the job?

## Part B · the whole project, end to end, looking for regressions
1. **Bot flows.** Simulate a full user session against the Worker with the fake D1 and a fake Telegram:
   - the start: `/start` → «Як читати прогноз» → oblast → raion → «Готово»;
   - settings: every screen, including removing a raion with confirmation, adding up to 3 raions and the 4th refused;
   - `/now`, `/hours` and `/recent`;
   - leaving: `/pause`, `/stop`, then `/start` again.
   
   List any dead button, wrong text or unhandled callback.
2. **Deliveries over a week.** Use a virtual clock and run every 5-minute cron tick from Sunday 12:00 to Saturday 12:00. Use the real ingest bodies from `test/fixtures` and two subscribers: «20:00 / при тривозі» and «21:00 / щоранку».
   - Check that each gets exactly the expected evening and morning messages: none on Fri/Sat evenings, no duplicates, none lost.
   - Repeat over 24–27 Oct 2026, across the DST change.
3. **Recovery from a quota outage.** Make D1 fail for a whole UTC day, as on 13 Oct, then recover. Check:
   - the bot answers with the «тимчасово» notice;
   - `/health` is 503;
   - after recovery, delivery, scrape, seed and cleanup resume by themselves;
   - no stale forecast is sent;
   - nothing is left stuck: outbox leases, `live_gap_to`, `extras_*` keys.
4. **Budget again.** Give D1 reads and writes per day for 5, 1 000 and 10 000 subscribers with the current code, compared with rounds 4 and 5. Measure them in the simulation of point 2, not only by formula.
5. **The research part** must still hold:
   - `scripts/reproduce.sh` from a fresh clone, with results matching the committed ones;
   - leakage tests, including the hourly ones;
   - every number in the README matches `results/`;
   - the README and `PREREG.md` describe the current bot and hosting (no stale claims about schedule, limits or features).
6. **Security, re-checked after all the changes:**
   - webhook and bearer checks;
   - nothing secret in `/health` or error responses;
   - no token or `data/bot/` anywhere in git history;
   - callback whitelisting for every new callback.
7. **Production, read-only:**
   - `npx wrangler d1 info shkilnyi-ranok`;
   - `npx wrangler d1 insights shkilnyi-ranok --timePeriod 1d --sort-type sum --sort-by reads --json`, and the same with `--sort-by writes`;
   - `curl https://shkilnyi-ranok.shkilnyi-ranok-worker.workers.dev/health`;
   - `gh run list --workflow forecast.yml -L 10`.
   
   The D1 quota was exhausted on 13 Oct and resets at 00:00 UTC; say which UTC day your numbers cover. Is the deployed Worker the same version as `main` (`wrangler deployments list`)? Is the production schema equal to `schema.sql` plus migrations 0002–0005? You may check that with read-only `SELECT name, sql FROM sqlite_master`.
8. **Tests.** Run `cd deploy/cloudflare/worker && npm test` and `PYTHONPATH=src pytest -q`, in the repo and on a fresh clone with Python 3.12. List flaky, slow or environment-dependent tests.

## Report format
1. **Part A:** a table of the 7 fixes · status · proof · whether a test fails when the fix is reverted · what is left.
2. **Part B:** a table of findings · **severity** · **file:line** · **problem** · **concrete reproduction** · **fix**, ordered by severity.
3. **The week simulation:** messages expected versus sent, per subscriber and per day.
4. **Budget:** reads and writes per day for the three scenarios, next to rounds 4 and 5.
5. **Verdict:**
   - Is it safe to leave running unattended for a month on the free plans?
   - What is the most likely next failure?
   - What are the 3 most important fixes?
6. **What you could not verify.**
