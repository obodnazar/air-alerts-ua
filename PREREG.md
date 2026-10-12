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
