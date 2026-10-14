# Pre-registration · written 12 Oct 2026, 00:35 Kyiv, before any model touched the test window

This file fixes what we will compare and what counts as success **before** looking at test results. If anything changes later, the change is listed at the bottom with its reason. Git history timestamps this file.

## Units
Raions with at least one raion-level alert of their own between 15 Dec 2025 and 31 Aug 2026, plus Kyiv city. Raions that only ever receive oblast-wide alerts (occupied territory) are excluded. Raions under alert at 08:00 on more than 60% of training weekdays are reported separately as "saturated": a forecast changes nothing there.

## Data and source switch
- Official feed (Vadimkin, deduplicated) up to 31 Aug 2026; @UkraineAlarmSignal (scraped) from 1 Sep 2026.
- Agreement of the two sources is measured on their overlap before modelling. If they disagree materially (episode starts matched within ±3 min below 80%), this is reported, and a source flag is added.
- Each source has a coverage end. No rows after it are treated as "no alert".

## Windows
- **Train:** weekdays 15 Dec 2025 → 31 Jul 2026 (raion-level regime only).
- **Validation (calibration, model choice, thresholds):** weekdays 1 → 31 Aug 2026.
- **Test:** school days 1 Sep → 9 Oct 2026, all weekdays (schools work on weekdays under martial law). Final models are refit on train+validation before the test run. The test is run once; any rerun is logged below.

## Questions and targets
- **M1:** is an alert active in the unit at 08:00 Kyiv time? Forecasts issued at 20:00 the previous evening and at 07:00.
- **M2:** given an alert active at 08:00, does the all-clear come by 10:00 (and by 11:00)? Overlapping episodes are merged.
- **M3:** in-person school hours (08:00–15:00) and "limbo hours" (hours after 08:00 during which the day's mode is undecided) under the MoE rule, for these policies:
  - A, status quo (react, control point 10:00);
  - B, plan the day from the 20:00 forecast;
  - C, early call at 08:00 using M2;
  - D, per-raion best control point chosen on train+validation;
  - Oracle, as the ceiling.

## Baselines (the model must beat the strongest, not the weakest)
- **M1:**
  - climatology: the unit's 08:00 alert rate over the previous 28 days;
  - persistence: state at the issue time;
  - rolling-Markov: P(alert at 08:00 | state at issue time) for the unit over the previous 42 days.
- **M2:**
  - pooled Kaplan–Meier of remaining time after 08:00, previous 60 days;
  - the same per oblast.

## Metrics
- **M1 / M2:**
  - Brier score and Brier skill score against each baseline;
  - log loss;
  - reliability (10 bins);
  - PR-AUC.
- **M1** is reported separately for "alert already active at issue time" and "no alert at issue time", because pooled scores flatter.
- **M2** adds a C-index on remaining time.
- **Uncertainty:** paired block bootstrap over days (blocks of 3 days, 1 000 resamples) for the BSS difference model − best baseline.

## Success criteria
- **M1 at 20:00:** BSS > 0 against the best baseline, with the 95% CI excluding 0.
- **M2:** BSS > 0 against pooled KM, CI excluding 0.
- **Model choice:** if a simpler model is within 1 SE of a complex one, the simpler one is reported as final.
- If a criterion fails, that is reported as the result, not hidden.

## Leakage controls (automated, in `tests/`)
1. Every feature carries an availability time. Assert `available_at <= issue_time` on every row.
2. Features recomputed from data truncated at the issue time equal the features from full data.
3. Planted-leak test: adding the label as a feature must trip the guard (AUC > 0.995 aborts the run).
4. Kaggle night reports become available at 09:00 Kyiv on the day the report window ends, not at `time_start`.

## Changes after registration
1. **12 Oct, 00:50, before the test run: unit rule refined.** The registered rule ("at least one own raion-level alert") did not remove occupied raions: the official feed gives them their own rows, identical to their neighbours' (all Donetsk raions have exactly 694 alerts; Berdianskyi, Melitopolskyi and Zaporizkyi share one series). Now excluded: Donetsk oblast as a whole (no raion zoning, near-permanent alert) and an explicit list of occupied raions in `data.py`. Also fixed: three raions renamed in 2024 (old names in the official feed, new ones in Telegram) are mapped to the new names.
2. **12 Oct, ~01:10, before the test run: two candidate features added, kept only if they improve validation (Aug 2026).**
   - (a) `country_night`: mean share of the night (00:00–07:00) under alert across units. Motivated by a check: after mass nights, alerts still active at 07:00 end before 08:00 more often (56% vs 64% still active), while new morning alerts are not rarer.
   - (b) Air Force (@kpszsu) threat posts that mention the unit's oblast in the last 1 h and 3 h before the issue time, by publish time.
   - Anonymous monitoring channels and Russian statements are not used: too sparse (3 MiG-31K posts in 5 weeks) and unreliable.
3. **12 Oct, ~00:55, before the test run: source switch moved from 1 Sep to 28 Aug.** Comparing the two alert sources day by day showed an outage in the official feed: 0 alerts on 30 Aug - 1 Sep (the volunteer feed has ~170 a day), half of them missing on 2 Sep. On days when the official feed works (3-5 Sep), 97-100% of its alerts match a Telegram alert within 2 minutes and vice versa. No other gap in Dec 2025 - Aug 2026 (daily official/volunteer ratio checked). Validation days 28-31 Aug now come from Telegram.
4. **12 Oct, ~01:55, before the test run: known limitation of the Air Force features.** The scraper's "edited" detector looked for the wrong HTML string, so historical posts carry no edit flag (fixed for future scrapes). A random sample of 60 pages (1 200 posts) shows 2.8% of threat posts were edited; examples look like wording fixes. An edit that added an oblast after publication would leak; the exposure is bounded by that 2.8% and the 1-SE rule decides whether these features are used at all.
5. **12 Oct, 01:50: the test run crashed after M1** (train and validation tables concatenated with duplicate row labels; the survival library refused them). Fixed with `ignore_index=True`, no other change. The run was repeated; M1 test numbers are identical in both runs (the pipeline is deterministic), so the repeat did not allow any choice based on test results. M2 and policy results come from the repeated run.

## Post-hoc work after the registered test (12 Oct, from ~10:40)

Everything below was designed **after** the test results were seen. It is reported separately and never mixed into the registered results. Each item is still evaluated out of sample: every forecast uses a model fitted only on days before the forecast day.

6. **All-clear per raion, refitted daily on the last 42 days** (`clearance.py`).
   - **Motivation:** the registered M2 was trained before the yellow/red reform and failed its criterion on test; the brief used one curve per oblast, and on 12 Oct all five Kharkiv raions got 61% while none cleared.
   - **Rolling evaluation** on weekdays 15 Sep – 12 Oct (520 alerts at 08:00), against the per-oblast Kaplan–Meier curve used before:
     - evening version: BSS +0.066 [+0.018, +0.114];
     - 08:00 version (level + elapsed): BSS +0.276 [+0.204, +0.325].
   - Both now feed the brief.
7. **Occupied raions from the DeepState map instead of a hand list** (`geo.py`): raions with >50% of their area occupied on 31 Aug 2026 (map history from cyterat/deepstate-map-data, raion boundaries from OPORA/map_2020). Outside Donetsk and Luhansk oblasts it selects exactly the seven raions of the hand list, so units and all results are unchanged; a test keeps the two in agreement.
8. **Air Force posts re-checked where they should matter most**: the 07:00 forecast for raions with no alert at 07:00 (new alerts), validation August, n=1 861. Brier 0.05257 with the posts vs 0.05251 without: no gain (−6e-5, SE 6e-4). Interpretation: alerts are declared from the same radar picture the Air Force posts describe, so by 07:00 that information is already in "is the oblast / the country under alert now".
9. **Later evening forecast and neighbouring raions (decided on validation, product unchanged).**
   - 20:00 vs 21:30 vs 22:00, Brier on August:
     - 0.0820 / 0.0829 / 0.0807;
     - on test (secondary): 0.1435 / 0.1485 / 0.1451.
     - No consistent gain: 22:00 is better on August, worse on test; 21:30 is worse on both.
   - Adjacent raions (from raion boundaries):
     - at 20:00 the gain is +0.4% on August, formally above 1 SE, and +0.1% on test;
     - at 07:00 it is below 1 SE, and worse on test.
     - The oblast and country shares already carry the spatial signal.
   - Kept the simpler model: 20:00, no neighbour features. Code: `experiments.py`; results: `results/posthoc_evening_neighbours.csv`.
10. **Keyword event watcher on Lachen (@lachentyt, 8 570 posts) and Air Force posts, Dec 2025 – Oct 2026** (`events.py`, product unchanged).
   - **Detection:** 64 mass-attack warnings from Lachen, 75 from the Air Force (mostly MiG-31K take-offs), 51 truce news items. Samples look right ("monitors: threat of a mass missile attack within 24 h"); rare false hits (a test launch).
   - **H1a, warning 12:00–20:00 → mass night:**
     - Kaggle mass report: 23% after a warning vs 15% without (lift 1.5); but only 17% of mass nights had one;
     - night alert load > 40%: no lift (0.98).
     - The two targets disagree: weak and unstable.
   - **H1b, warning as a feature at 20:00:** no gain on validation (−1e-5); rejected by the registered rule.
   - **H2, truce news clusters (n=7, from Dec 2025):** alert hours per unit fell in the 3 days after 6 of 7 clusters (mean −0.6 h/day). Too few events, and confounded by weekly patterns, so no causal claim.
   - **Use in the product:** a banner in the brief ("situation changed"), not a model feature.

## Independent audit and corrected re-run (12 Oct, ~15:30)

11. **An external audit (OpenAI Codex, brief in `docs/codex-audit-prompt.md`) found data and label bugs that touched the registered test.** Each was reproduced by hand before fixing; each has a regression test.
    - **Parser:** 752 multi-section posts were read as one event.
    - **Timestamps:** times were floored to the minute (a 40-second peek).
    - **Labels:** censored all-clears were scored as cleared.
    - **Baseline:** the KM baseline used all training days instead of the registered 60.
    - **Stitching:** alerts crossing the source switch could vanish.
    - **Reproducibility:** a fresh clone chose a different feature set.

    **Only data, labels and the registered baseline were corrected.** Models, features, windows, thresholds and the 1-SE rule are unchanged. The full pipeline was re-run once.

    | | before the fixes | after the fixes | criterion |
    |---|---|---|---|
    | M1 07:00 vs strongest | +6.7% [+1.3, +13.0] | +7.8% [+2.7, +13.3] | met |
    | M1 20:00 vs strongest | +2.6% [−3.2, +8.1] | +5.1% [+2.7, +8.0] | now met |
    | M2 vs KM (last 60 days) | +9.2% [−1.0, +21.0] | +12.2% [+1.8, +24.7] | now met |

    **Caution:** two criteria flipped to "met" after a correction made after seeing the test, so they deserve less trust than the 07:00 result, which held in both runs.
    - On corrected labels, the Air Force features pass the 1-SE rule for M2 on validation (gain 0.0022, SE 0.0017) and are used there.
    - Post-hoc item 6 weakened: the per-raion evening all-clear is +5.0% [−1.5, +13.3], no longer significant.

    The first run is kept in `results/archive_before_audit/` and in git history.

## Post-hoc: hourly risk for the bot (12 Oct, from ~21:00, after submission)

12. **P(alert active at a whole hour, today or tomorrow), per raion** (`hourly.py`). Asked for a bot button: "is an alert likely at 15:00?". Designed after every result above was known, so it is post-hoc by construction. **The spec and the display rule below were written down before the backtest was run.**
    - **Target:** alert active in the unit at hour *h*:00 (Kyiv), for every whole hour from the issue time to 23:00 tomorrow (leads 1–47 h).
    - **Baseline (shown to users when the model does not beat it):** the raion's share of the previous 28 days with an alert at *h*:00.
    - **Model:** the baseline, adjusted for the state at the issue time.
      - Alert on now, running *e* min: P = S(L | e) + Σ<sub>τ</sub> f(τ | e) · P<sub>new</sub>(L − τ). S is the Kaplan–Meier survival of alert durations in the previous 42 days (pooled, 24 h+ censored), conditional on the time already elapsed; f is the matching density of the end time.
      - No alert now: P = P<sub>new</sub>(L) = 1 − (1 − clim)<sup>C(L)</sup>, where C(L) is the share of the alerts covering *h*:00 that start within the last L minutes before it. C comes from the raion's start-time profile by hour (previous 28 days, shrunk to its oblast) and the duration curve S. Under Poisson starts this is exact, and it tends to the baseline as L grows.
      - Everything is computed from data before 00:00 of the issue day, plus the state at the issue time.
    - **Backtest:** every day 15 Sep → end of data, weekdays and weekends, issued at every whole hour; Brier skill vs the baseline per lead bucket (1, 2, 3, 4–6, 7–12, 13–24, 25–47 h), separately for "alert on" and "no alert" at issue; paired block bootstrap over issue days (3-day blocks, 1 000 resamples), as for M1.
    - **Display rule:** the bot shows the model for a (state, lead bucket) only if the 95% CI of its Brier skill is above 0 on the whole backtest **and** the point estimate is above 0 in both halves (15–28 Sep, 29 Sep → end). Everywhere else it shows the plain 28-day share, labelled as history.
    - **Result** (run once, 12 Oct ~21:00; 104 units, 28 issue days 15 Sep – 12 Oct, 2.4 M forecasts; `results/posthoc_hourly_summary.csv`):

      | state at issue | lead | n | alert rate | BSS vs history [95% CI] | halves | shown |
      |---|---|---|---|---|---|---|
      | alert on | 1 h | 16 260 | 61% | **+16.0%** [+7.0, +24.2] | +18.8 / +13.0 | model |
      | alert on | 2 h | 16 233 | 55% | **+8.0%** [+2.0, +13.9] | +10.4 / +5.5 | model |
      | alert on | 3 h | 16 207 | 53% | **+4.8%** [+0.8, +9.0] | +6.3 / +3.2 | model |
      | alert on | 4–6 h | 48 470 | 52% | **+2.3%** [+0.4, +4.3] | +3.2 / +1.3 | model |
      | alert on | 7–12 h | 96 084 | 49% | +0.4% [+0.1, +0.6] | +0.5 / +0.3 | model |
      | alert on | 13–47 h | 365 226 | 48% | 0.0% | ≈ 0 | history |
      | no alert | 1 h | 53 212 | 12% | +1.9% [−1.0, +5.6] | +1.4 / +2.5 | history |
      | no alert | 2–12 h | 580 334 | 14–16% | +0.1 … +0.8%, all CIs include 0 | | history |
      | no alert | 13–47 h | 1 214 742 | 16–17% | 0.0% | | history |

      - **What this means:** the model helps only while an alert is on, mostly for the next 1–3 hours (is it still on, or on again). With no alert now, it predicts the next one no better than "how often there was an alert at that hour in the last 4 weeks", so the bot shows that share, labelled as history.
      - **Calibration:** the model is too low. With an alert on, it gives 57% at 1 h against 61% observed, and 47% against 55% at 2 h; with none, 8.7% against 12.0% at 1 h. Alerts cluster more than independent (Poisson) starts allow. A recalibration is the obvious next step; it is not done here, because it would be tuned on this same backtest.
      - Weekdays and weekends, all leads pooled: +0.6% [+0.3, +1.1] and +0.3% [0.0, +0.9].
    - **Product:** live state comes from the Worker's own 5-minute scrape of t.me/s; the parameters and the gates are published twice a day (`publish.py`), never computed by frequent GitHub Actions runs.
13. **Hourly risk, version 2: a stacked logistic that knows about waves** (`hourly.py`, `H2`). Asked on 14 Oct: the hour screen should give a model probability, not only history. Item 12 showed why it did not: with no alert now, the model was no better than history, and it was too low everywhere because "alerts cluster more than independent (Poisson) starts allow". Post-hoc by construction, after item 12's results. **This spec, the features and the display rule were written down before the version-2 backtest was run.**
    - **Target, issue times, leads and buckets:** as in item 12. Hour *h*:00 Kyiv, issued at every whole hour, leads 1–47 h, the same 7 buckets, and "alert on" and "no alert" at issue reported apart.
    - **Model:** for each (state at issue, lead bucket), a logistic regression
      logit P = a + b₁·logit(P<sub>H1</sub>) + b₂·logit(clim) + b₃·occ6 + b₄·occ24 + b₅·oblast_on + b₆·country_on [+ b₇·log(1 + elapsed) when an alert is on].
      - P<sub>H1</sub> is the item-12 model, and clim is its baseline (both clipped to [0.002, 0.998]).
      - occ6 and occ24 are the share of the last 6 h and 24 h the raion spent under alert (clustering in time).
      - oblast_on and country_on are the shares of the oblast's served raions and of all served raions under alert at the issue time (a wave in progress).
      - elapsed is the minutes the current alert has run.
      - Every feature uses only alerts that started before the issue time T, cut at T.
    - **Fitting (rolling, no look-ahead):** for issue day *d*, the coefficients are fitted on pairs issued in the 14 days before *d* whose target hour is before 00:00 of *d*. An outcome must be known by then. L2 penalty with C = 1. At most 80 000 training rows per (state, bucket), sampled with a fixed seed. Refitted every day.
    - **Backtest:** every day 15 Sep → end of data, exactly as item 12. Brier skill vs the same history baseline per (state, bucket), with the paired block bootstrap (3-day blocks, 1 000 resamples) and the two halves. The skill vs H1 is reported too.
    - **Display rule (unchanged in kind):** for a (state, bucket), the bot shows H2 if its 95% CI vs history is above 0 and both halves are above 0. Otherwise it shows H1 if item 12's gate is open, otherwise history. A negative result is reported as such.
    - **Calibration:** mean forecast vs observed rate per (state, bucket), to check whether H2 fixes the under-forecast found in item 12.
