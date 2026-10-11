# School mornings under air raid alerts

**Forecasting, per raion, whether a school day will start under alert and whether the all-clear will come in time, evaluated on the latest five weeks with no leakage.**

KSE Agentic AI School 2026, Stage 2. Time series analysis of air raid alerts in Ukraine.

> **Коротко українською.** Школа щоранку вирішує за правилом МОН (лист №1/19575 від 11.09.2026). Якщо о 08:00 тривога, день починається дистанційно. Якщо відбій приходить до контрольної точки (наприклад, 10:00), діти йдуть на очне навчання, інакше весь день дистанційний. Ми прогнозуємо по кожному району ймовірність тривоги о 08:00 (о 20:00 напередодні і о 07:00), ймовірність відбою до контрольної точки і ціну різних стратегій школи. Перевірено на 1.09–9.10.2026.
>
> Результати:
> - **Прогноз о 07:00 значуще кращий** за найсильніше просте правило: +6.7%.
> - Прогноз о 20:00 і модель відбою на тесті значущої переваги **не показали**.
> - Найбільший виграш для шкіл дає **вибір контрольної точки під свій район**.
> - Порайонні тривоги зберегли **1 934 шкільні ранки**.

## The decision

Ministry of Education letter №1/19575 (11 Sep 2026) sets the rule, checked in the primary source:

- if an alert is active when the school day starts, the day starts remotely;
- the school sets a **control point** in advance (e.g. 10:00): an all-clear before it means in-person lessons after a ~60 min transition, an all-clear after it means the whole day is remote;
- more than 2 hours of alerts per school day for several days in a row means changing the learning model.

So three questions matter, for each raion:

| | Asked at | Question |
|---|---|---|
| **M1** | 20:00 the evening before, and 07:00 | Will an alert be active at 08:00? |
| **M2** | 08:00, if an alert is on | Will the all-clear come by 10:00 (11:00, …)? |
| **M3** | ahead of time | Which strategy gives the most in-person hours with the least time in limbo? |

## Data, and what was wrong with it

| Source | Used for | Finding |
|---|---|---|
| [Vadimkin dataset](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset), official feed | 15 Dec 2025 → 27 Aug 2026 | **38% exact duplicate rows**. **Outage 29 Aug – 2 Sep** (0 alerts on 30 Aug – 1 Sep while the volunteer feed has ~170/day). **Stops on 7 Sep 2026**, when its source channel went silent after the yellow/red reform |
| Telegram [@UkraineAlarmSignal](https://t.me/s/UkraineAlarmSignal), scraped (61 300 posts, 15 Aug → 11 Oct) | 28 Aug → now | Same state signal, with **yellow/red level** and level changes. When the official feed works, the two agree on **96–97% of alert starts within 2 min, both ways** ([source_agreement.csv](results/source_agreement.csv)) |
| Kaggle [Massive Missile Attacks on Ukraine](https://www.kaggle.com/datasets/piterfm/massive-missile-attacks-on-ukraine) | days since a mass night, drones in 7 days | A night's report is usable only after it is published (time_end + 2 h) |
| Telegram @kpszsu (Air Force), scraped (36 560 posts, Dec → Oct) | candidate feature | Did not pass validation, see below |

Other traps found on the way:
- Three raions **renamed in 2024** had different names in the two sources.
- **Occupied raions** pass a naive "has its own alerts" filter, because the official feed repeats oblast-wide alerts per raion. Donetsk oblast (not zoned, near-permanent alert) and occupied raions are excluded, which leaves **104 units: 103 raions + Kyiv**.
- The series has structural breaks:
  - raion-level alerts nationwide from 15 Dec 2025;
  - jet-drone "streaks" from mid-Aug 2026;
  - restricted Air Force reporting from 11–12 Aug;
  - yellow/red levels from 4–6 Sep;
  - voice-only all-clear from 18 Sep.

Details: [docs/research/01-data-landscape.md](docs/research/01-data-landscape.md).

## Method

Everything a model sees is computed **as of the issue time**. Plain-language walk-through: [docs/how-it-works.md](docs/how-it-works.md).

- **M1 features:**
  - the raion's state now and the minutes since the last alert;
  - alert minutes in 6 h / 24 h / 7 d;
  - share of the oblast and of the country under alert now;
  - the raion's 08:00 alert rate over 28 days;
  - a rolling 42-day Markov probability;
  - days since the last mass night and drones in the last 7 days (published reports only);
  - at 07:00, also the night's alert minutes.
- **M1 models:** logistic regression and gradient boosting (isotonic-calibrated).
- **M2:** Kaplan–Meier baselines (pooled, per oblast), logistic regression, Weibull AFT; time from 08:00 to the all-clear with right-censoring.
- **M3:** in-person hours (08:00–15:00) and "limbo" hours under the MoE rule for:
  - **A**, status quo;
  - **B**, plan the day at 20:00;
  - **C**, call the day at 08:00 using M2;
  - **D**, a per-raion control point chosen on history;
  - **Oracle**, as the ceiling.

**Evaluation was pre-registered** in [PREREG.md](PREREG.md) before the test window was touched:
- train 15 Dec – 31 Jul;
- validation August (model, features, thresholds);
- **test 1 Sep – 9 Oct 2026, run once**;
- baselines: climatology, persistence, rolling Markov; the model must beat the **strongest** one;
- paired block bootstrap over days.

Every change after registration is listed there with its time and reason, including one crashed test run.

**Leakage controls** ([tests/test_leakage.py](tests/test_leakage.py), 8 tests):
- `available_at <= issue_time` on every row;
- features recomputed on a world **truncated at the issue time** equal the full-data features;
- a **planted leak** must trip the guard (AUC > 0.995 aborts);
- a check that the truncation test itself detects a feature that peeks 12 h ahead;
- attack reports are never available before their window ends;
- Air Force post counts use only earlier posts.

## Results (test: 1 Sep – 9 Oct 2026, 104 units, 29 weekdays)

![skill](figures/test_skill.png)

| Question | Model | vs strongest baseline | 95% CI | Pre-registered criterion |
|---|---|---|---|---|
| Alert at 08:00, issued **07:00** | logistic | **+6.7%** Brier skill (vs rolling Markov) | +1.3% … +13.0% | **met** |
| Alert at 08:00, issued 20:00 | logistic | +2.6% (vs rolling Markov) | −3.2% … +8.1% | not met |
| All-clear by 10:00 | logistic | +9.2% (vs Kaplan–Meier) | −1.0% … +21.0% | not met |

- The test period was much more intense than training: an alert was active at 08:00 on **25.4%** of raion-mornings, against 12.2% in training. Against plain climatology the 07:00 model gains +21.9% and the 20:00 model +4.5%.
- **Gradient boosting lost to logistic regression** on validation and on test, so the simpler model is reported.
- The all-clear model scored +29% on validation but only +9% (not significant) on test. **The yellow/red reform changed how alerts end** (voice-only all-clear from 18 Sep), and a model trained before it degrades.
- **Air Force threat posts did not help.** "How many Air Force posts mentioned your oblast in the last 1–3 h" improved validation by less than one standard error, so by the registered 1-SE rule it was dropped. The two strongest public competitors found the same about Telegram text for alert onset.

### All-clear by alert colour (since 6 Sep)

If an alert is on at 08:00, this is the chance of the all-clear by each time (Kaplan–Meier, 855 alerts; [levels_clear_by_time.csv](results/levels_clear_by_time.csv)):

| alert at 08:00 | already running | by 09:00 | **by 10:00** | by 11:00 | by 12:00 |
|---|---|---|---|---|---|
| yellow | < 30 min | 64% | **77%** | 85% | 92% |
| yellow | 30 min – 2 h | 48% | **61%** | 76% | 82% |
| yellow | > 2 h | 36% | **48%** | 63% | 65% |
| red | < 30 min | 52% | **65%** | 83% | 89% |
| red | 30 min – 2 h | 43% | **59%** | 80% | 82% |
| red | > 2 h | 13% | **24%** | 33% | 37% |

The level used is the one **in force at 08:00**, not the maximum over the alert: an alert that turned red at 09:00 was not known to be red at 08:00. Fitted only on earlier days, the colour improves the all-clear forecast by +5.2% Brier skill (407 weekday alerts from 21 Sep).

![levels](figures/levels_clear_curves.png)

### What it means for a school (M3)

![policies](figures/test_policies.png)

| Strategy | In-person hours / day | Limbo min / day | Fully remote days |
|---|---|---|---|
| A · status quo, control point 10:00 | 5.95 | 19.5 | 11.7% |
| B · plan a remote day at 20:00 | = A: never triggered | | |
| C · call the day at 08:00 with M2 | 5.93 | 16.7 | 12.2% |
| **D · per-raion control point** | **6.06** | 24.4 | **8.4%** |
| Oracle (knows the all-clear time) | 5.95 | 5.5 | 11.7% |

- **Planning a remote day the evening before never pays off.** On validation its value rises with the threshold until it stops firing: a wrongly cancelled school day costs more than the uncertainty it removes. The evening forecast is information for parents, not a trigger.
- **The biggest lever is the control point.** Choosing it per raion from history (D) gives more in-person hours than the status quo, at the price of a few more minutes of waiting.
- **The 08:00 call (C) cuts limbo** but not in-person hours, because the all-clear model is not sharp enough on the new regime.

### The price of alerts for schools

From [results/cost_summary.json](results/cost_summary.json):

- **Zoning dividend.** 15.0% of raion school mornings (Jan–May and Sep–Oct 2026) started under alert. If every alert covered the whole oblast, it would be 29.4%: raion-level alerts **saved 1 934 school mornings**.
- **Yellow vs red.** Since 8 Sep, yellow (drone) alerts took **14.0%** of weekday school hours (08:00–15:00) and red **12.0%**. Under КМУ №1092 businesses with shelters may work during yellow, but schools shelter at both levels. Yellow alerts are where school time is lost relative to the rest of the economy.
- **Control point trade-off.** Across 65 mid-intensity raions, moving the control point from 09:00 to 12:00 cuts fully remote days from 10.6% to 4.1% and raises waiting from 9 to 21 minutes a day.

![control point](figures/cost_control_point.png)

## Live test: Monday 12 Oct 2026

The forecast for this morning was committed **at 00:40, before 08:00** ([brief](results/live/brief_2026-10-12_2000.md)). A 07:00 update and the checks at 08:00 and 10:00 follow: see [results/live/](results/live/).

*(to be filled after 10:07)*

## Evening brief

`python -m airalerts.brief --day 2026-10-13` writes one line per raion: the chance of an alert at 08:00, the chance of an all-clear by 10:00, the chance of a fully remote day, and plain advice. It also includes the colour × elapsed-time table for the 08:00 update. The text comes from a template, with no LLM: every number in it comes straight from the models.

## Limitations

- **One evaluation window of 29 weekdays in an unusually intense period.** The intervals above reflect that.
- **The all-clear model was trained before the yellow/red reform.** Only ~5 weeks of post-reform data exist.
- **The source switch on 28 Aug** means training and test labels come from different, though 96–97% consistent, feeds.
- **Edited posts.** Historical Telegram posts carry no "edited" flag (a scraper bug, fixed). A sample shows 2.8% of Air Force threat posts were edited, and those features were not used in the end.
- **Excluded units.** Occupied raions are listed by hand, and Donetsk oblast is excluded.
- **Scope.** Not a replacement for official alerts. Uses no real-time threat positions.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export PYTHONPATH=src
.venv/bin/python -m airalerts.sources.vadimkin                                   # official + volunteer feeds
.venv/bin/python -m airalerts.sources.telegram UkraineAlarmSignal --since 2026-08-15
.venv/bin/python -m airalerts.sources.telegram kpszsu --since 2025-12-01        # optional
# Kaggle: download missile_attacks_daily.csv to data/raw/kaggle/
.venv/bin/python -m airalerts.parse_signal
.venv/bin/python -m pytest -q                       # leakage tests
.venv/bin/python -m airalerts.evaluate              # validation only
.venv/bin/python -m airalerts.evaluate --test       # the registered test run
.venv/bin/python -m airalerts.levels && .venv/bin/python -m airalerts.cost && .venv/bin/python -m airalerts.figures
```

## Repository

| Path | What |
|---|---|
| `src/airalerts/sources/` | Telegram web-preview scraper (no login), dataset downloader |
| `src/airalerts/parse_signal.py` | posts → alert episodes with level timelines |
| `src/airalerts/data.py` | one alert store per raion, source switch, exclusions |
| `src/airalerts/dataset.py`, `attacks.py`, `airforce.py` | as-of-time features |
| `src/airalerts/models.py`, `policies.py`, `evaluate.py` | models, MoE decision rule, registered evaluation |
| `src/airalerts/levels.py`, `cost.py` | all-clear by colour; price of alerts for schools |
| `src/airalerts/brief.py`, `live.py` | evening brief; live forecast and check |
| `PREREG.md` | the registered evaluation and every later change |
| `docs/` | research, the five problems considered, architecture, plain-language explanation |

## Responsible use

The project uses only the start and end times of public alerts, their published level, and published daily reports. It does not show or use real-time movement of threats or air defence (Criminal Code art. 114-2). It is a research project and does not replace official alert signals.
