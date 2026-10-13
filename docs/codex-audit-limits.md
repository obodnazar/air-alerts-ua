# Codex audit, round 4: free-tier limits and whether the architecture holds

**Do not change code.** Report findings only.

## Why
On 13 Oct 2026 the bot went down for the whole day. `Store.applySeed` compared every alert with every `json_each` row: about 14 M row reads in one call. That used up D1's free daily limit of 5 M row reads, so every query failed until 00:00 UTC. It is fixed in `1a90fab` (a keyed `seed_keys` table). The tests had missed it, because the fake D1 does not count rows read.

The question now: **can any other part of the architecture hit a Cloudflare or GitHub limit, and will the whole thing keep working day after day?**

## Architecture (read the code, do not trust this summary)
- **GitHub Actions:** `.github/workflows/forecast.yml` runs `src/airalerts/botapp/publish.py` 6 times a day. It POSTs to `/ingest` four kinds: `evening`, `morning`, `hourly` and `alerts`. `alerts` is a 7-day history of ~4 000 rows.
- **Worker:** `deploy/cloudflare/worker/src/`
  - `index.js`: fetch for `/telegram`, `/ingest` and `/setup`; a cron every 5 min that scrapes t.me/s, then delivers; delivery also runs right after `/ingest`.
  - `store.js`: all SQL.
  - `live.js` and `signal.js`: the scrape.
  - `router.js` and `ui.js`: the bot.
- **D1 schema:** `schema.sql` and `migrations/`.

## Limits to check (verify the current numbers on Cloudflare's and GitHub's docs pages, and cite them)
**Workers Free:**
- requests per day;
- CPU time per invocation;
- subrequests per invocation;
- cron triggers;
- request body size;
- memory.

**D1 Free:**
- **rows read per day** (5 M, account-wide), the main one;
- rows written per day;
- queries per invocation;
- storage;
- maximum SQL statement size and bound parameters;
- batch limits;
- maximum JSON size passed as one parameter.

**GitHub Actions:**
- minutes (the repo is public now);
- scheduled-run delays;
- the cache size;
- the ToS on using Actions as part of a serverless app (our use: a twice-daily forecast plus 4 extra runs).

**Telegram:** about 30 messages per second, and per-chat limits.

## What to do
1. **Row reads per query.** For every SQL statement in `store.js`:
   - give the **row reads per call** as a formula in table sizes (alerts, outbox, subs, units, hits, forecasts, seed_keys);
   - show the `EXPLAIN QUERY PLAN` (is an index used? any full scan or correlated scan over `json_each`?);
   - give how many times it runs per day.
2. **Daily budget.** Estimate total row reads per day in three scenarios:
   - (a) today: about 5 subscribers, about 6 000 alerts kept;
   - (b) 1 000 subscribers with heavy bot use;
   - (c) 10 000 subscribers.
   
   Count the scrape every 5 min, the deliveries, every `/ingest` kind, `/now`, `/hours`, `/recent` and the rate-limit table. Say at what scale each limit breaks.
3. **Table growth.** Does each table stay bounded? Check alerts (the 14-day cleanup), outbox (never deleted?), hits, forecasts and state. Find any query whose cost grows with history.
4. **Per-invocation limits.**
   - Can one cron run exceed 50 subrequests or 50 D1 queries? It does a scrape of up to 5 pages plus delivery of up to 40 messages.
   - Check an `/ingest` that triggers delivery.
   - Check the CPU limit when parsing 5 pages or building 40 messages.
   - Check whether a 4 000-row JSON parameter or the request body is within limits.
5. **Make the test catch the real cost.** Propose a change so the tests catch row reads. One option: wrap `node:sqlite` and sum `EXPLAIN QUERY PLAN` scans. Another: a test that fails on a `SCAN` of a large table, or a timing test on realistic sizes.
6. **Failure modes.** What happens when a limit is hit?
   - Does the bot degrade gracefully (the new "database is down" notice)?
   - Does delivery resume after the reset?
   - Are any messages lost or duplicated?
   - Do GitHub retries amplify the load?
7. **Monitoring.** Is there any alert before a limit is reached? Propose the cheapest option: a daily `rows_read_24h` check in Actions, or a counter in D1.
8. **Verdict.** Will this run reliably every day on the free plans? If not, what is the smallest change that makes it so? Options to weigh:
   - fewer `alerts` seeds;
   - a smaller window;
   - caching in Workers KV;
   - moving the live state to a Durable Object;
   - Workers Paid.

## Report format
A table: **severity** · **file:line** · **limit at risk** · **rows/queries/subrequests per call and per day (numbers)** · **at what scale it breaks** · **fix**.

Then:
- the daily budget table for scenarios a, b and c;
- the 3 most important fixes;
- what is fine;
- what you could not verify.

Run `cd deploy/cloudflare/worker && npm test` and `PYTHONPATH=src pytest -q`. If you can, run `npx wrangler d1 insights shkilnyi-ranok --timePeriod 1d --sort-by reads --json` and `npx wrangler d1 info shkilnyi-ranok` to compare your estimates with real numbers. These commands are read-only.
