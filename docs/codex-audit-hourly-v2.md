# Codex audit, round 7: the hourly risk model version 2 (maths, method, implementation)

**Do not change code, `results/`, `PREREG.md` or production data.** Report findings only. Use a temporary copy for anything that writes.

## What was built
The bot's «🕐 Ризик по годинах» answers: "will an alert be on in my raion at h:00 today or tomorrow?". There are two versions:
- **Version 1** (PREREG item 12, `src/airalerts/hourly.py`, `params_asof`, `coverage_share`, `forecast`):
  - no alert now: P = 1 − (1 − clim)^C(L);
  - alert on now: P = S(L|e) + Σ f(τ|e)·P_new(L − τ).
  
  It was too low, and with no alert now no better than history.
- **Version 2** (PREREG item 13, written before its backtest in commit `220edc9`; code in `81ee2ca`) is a logistic regression per (state at issue, lead bucket):
  logit P = a + b₁·logit(P_H1) + b₂·logit(clim) + b₃·occ6 + b₄·occ24 + b₅·oblast_on + b₆·country_on [+ b₇·log(1+elapsed) when on].
  - It is refitted daily on the previous 14 days, using pairs whose target is before 00:00 of the issue day.
  - Code: `wave_features`, `occupancy`, `design`, `day_pairs`, `fit_v2`, `predict_v2`, `backtest_v2`, `coef_asof`, `payload`.
  - The Worker runs it in `deploy/cloudflare/worker/src/hourly.js` (`occupancy`, `waveFeatures`, `forecastV2`, `hourRisk`) and `router.js`, with its own live scrape.
  - **Display rule:** show v2 where its gate is open (95% CI > 0 and both halves > 0), else v1 where item 12's gate is open, else the history share.
- **Result** (`results/posthoc_hourly_v2_summary.csv`): it beats history up to 12 h ahead in both states, e.g. +7.1% [+4.8, +9.9] at 1 h with no alert now. The mean forecast now matches the observed rate.

## Part A · the maths
1. **Version 1 formulas:**
   - Derive P_new = 1 − (1 − clim)^C(L) under the stated assumption (Poisson starts, durations from S). Is it exact as claimed?
   - When is it wrong? Clustering, non-stationary rates, the overlap of alerts.
   - Is the "alert on" formula a correct renewal-type decomposition? Is the conditioning S(·|e) on elapsed time done right?
   - Is censoring at 24 h handled consistently in `km_grid`?
2. **Version 2 as a model:**
   - Is stacking logit(P_H1) and logit(clim) with these covariates sound, or are the two collinear? Check the variance inflation factor or the condition number on real training data.
   - Do the signs and sizes of the fitted coefficients make sense? Use `coef_asof` for a few days.
   - Is L2 with C = 1 on unscaled features reasonable, given that occ and shares are in [0, 1] and logits are in about [−6, 6]? Would scaling change results?
   - Is one model per lead bucket better than lead as a feature?
3. **Probability quality.** The PREREG reports Brier skill only. Also compute:
   - log loss;
   - a reliability diagram or ECE per (state, bucket);
   - a Brier decomposition (reliability / resolution).
   
   Is v2 sharper and better calibrated, or just recalibrated?
4. **Evaluation validity:**
   - **Leakage in the training window:** pairs issued in the 14 days before d with target < 00:00 of d. Can any feature or label in training use information after the issue time of the evaluated forecast?
   - **Overlap of training and test:** day d's own pairs must never be in its training set.
   - **Bootstrap:** the forecasts overlap heavily (the same target hour forecast from 24+ issue times, and 104 raions on one alert wave). Is a paired 3-day block bootstrap over issue days enough? Would blocks by week, or clustering by target day, change the CIs?
   - **Multiple comparisons:** 14 cells, of which 10 are open. How many would pass by chance?
   - **Selection:** the features were chosen after item 12's results on the same period. How much optimism does that add? Suggest an honest check, such as days after 13 Oct only, or a temporal holdout.
5. **Alternatives.** Name the strongest established models for this problem and say whether they would likely beat v2 and at what cost:
   - a self-exciting (Hawkes) onset process;
   - an inhomogeneous semi-Markov or alternating renewal process;
   - a discrete-time hazard GLM;
   - gradient boosting on the same features.

   Back the answer with literature or a quick experiment on the same backtest.

## Part B · the implementation
6. **Python ↔ JS parity.** `test/fixtures/hourly_v2_parity.json` covers 80 rows and 6 occupancy checks.
   - Is that enough? Check bucket edges, elapsed = 0, a stuck alert (elapsed > 24 h), a raion with no alerts, an oblast with one raion, DST days and missing coefficient cells (`None`).
   - Python measures merged chains, the Worker measures alert rows merged by union. Can they differ? Think of a chain that started before the 48 h fetch window, or a row with `ended` null that Python has closed.
   - **Live versus backtest features:** the Worker's `oblast_on`/`country_on` come from its open alerts, Python's from `state_at`. Is the oblast mapping (`ob` from `store.oblast_of`) the same set of units as in training? Served units versus eligible units?
7. **Leakage tests.** `tests/test_leakage.py::test_hourly_inputs_equal_on_truncated_world` now includes the wave features.
   - Revert the cut in `occupancy` (use full `e` instead of `min(e, t)`) and check that a test fails.
   - Is there a test that the training rows of `fit_v2` have outcomes known at fit time?
8. **Publishing and validation:**
   - `payload` now fits coefficients in GitHub Actions (~4 s locally) and sends them with `gates2`.
   - `invalidHourly` validates `coef`, `gates2` and `ob`. Can a malformed but accepted body make `hourRisk` throw, or give NaN or values outside [0, 1]?
   - What happens with an old body without `coef`?
9. **Cost.** The hour screen now reads all open alerts plus one raion's last 48 h. Give the rows read per screen and per day for 5 / 1 000 / 10 000 users, as in rounds 4–6.
10. **UI honesty.** For each version, does the screen say where the number comes from (model vs history)? Is "≈" plus "за історією району" clear for a teenager or a parent? Never a hint to go out while an alert is on.
11. **Reproduce.** From a fresh clone, run `python -m airalerts.hourly --v2` and compare with the committed summary. Then run `npm test` and `PYTHONPATH=src pytest -q`.

## Report format
1. **Part A:** for each point, a verdict (sound / questionable / wrong), the maths or the experiment behind it, and a concrete improvement.
2. **Calibration and log-loss table** for v1, v2 and history per (state, bucket).
3. **Part B:** a findings table with **severity** · **file:line** · **problem** · **reproduction** · **fix**.
4. **Verdict:**
   - Are the v2 numbers trustworthy enough to show to users?
   - What is the most likely way they mislead?
   - What are the 3 most valuable next steps?
5. **What you could not verify.**
