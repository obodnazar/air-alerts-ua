"""Models, baselines and metrics for M1 (alert at 08:00) and M2 (all-clear by the control point)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter, WeibullAFTFitter
from lifelines.utils import concordance_index
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

M1_FEATURES = ["active_now", "mins_since_end", "unit_min_6h", "unit_min_24h", "unit_min_7d", "night_min",
               "oblast_share_now", "country_share_now", "clim28", "markov42", "days_since_mass", "drones_7d",
               "mass_30d"]
EXTRA = ["country_6h", "af_oblast_1h", "af_oblast_3h", "af_all_3h"]  # candidates, kept only if validation improves
M2_FEATURES = ["elapsed", "night_min", "unit_min_7d", "oblast_share_now", "country_share_now",
               "days_since_mass", "drones_7d"]


class LeakageGuard(RuntimeError):
    pass


def guard(y, p, name: str) -> None:
    """A real forecast of alerts cannot be near-perfect; if it is, something leaked."""
    if len(set(y)) > 1 and roc_auc_score(y, p) > 0.995:
        raise LeakageGuard(f"{name}: AUC > 0.995 — almost certainly leakage, run aborted")


# ---------- M1 ----------

def _X(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    X = df[cols].astype(float).copy()
    if "clim28" in X:
        X["clim28"] = X["clim28"].fillna(X.get("markov42", 0))
    return X.fillna(0).to_numpy()


def fit_m1(train: pd.DataFrame, features: list[str] = M1_FEATURES) -> dict:
    X, y = _X(train, features), train.y.astype(int).to_numpy()
    logit = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000)).fit(X, y)
    hgb = CalibratedClassifierCV(
        HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0,
                                       random_state=0),
        method="isotonic", cv=3,
    ).fit(X, y)
    persist = train.groupby("active_now").y.mean().to_dict()
    return dict(logit=logit, hgb=hgb, persist=persist, features=features)


def predict_m1(fitted: dict, df: pd.DataFrame) -> pd.DataFrame:
    X = _X(df, fitted["features"])
    out = pd.DataFrame(index=df.index)
    out["climatology"] = df.clim28.fillna(df.markov42).clip(0.001, 0.999)
    out["persistence"] = df.active_now.map(fitted["persist"]).astype(float)
    out["rolling_markov"] = df.markov42
    out["logit"] = fitted["logit"].predict_proba(X)[:, 1]
    out["hgb"] = fitted["hgb"].predict_proba(X)[:, 1]
    return out.clip(0.001, 0.999)


# ---------- M2 ----------

def km_prob(train: pd.DataFrame, horizon: float) -> float:
    kmf = KaplanMeierFitter().fit(train.remaining, train.event)
    return float(1 - kmf.predict(horizon))


def fit_m2(train: pd.DataFrame, horizon: float = 120, features: list[str] | None = None) -> dict:
    feats = features or M2_FEATURES
    known = train[train.event | (train.remaining > horizon)]  # censored before the horizon: outcome unknown
    X = known[feats].astype(float).fillna(0)
    y = (known.remaining <= horizon).astype(int)
    logit = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000)).fit(X, y)
    aft_df = train[feats + ["remaining", "event"]].copy().fillna(0)
    aft_df["remaining"] = aft_df.remaining.clip(lower=1)
    aft = WeibullAFTFitter(penalizer=0.01).fit(aft_df, duration_col="remaining", event_col="event")
    oblast_km = {o: km_prob(g, horizon) for o, g in train.groupby("oblast") if len(g) >= 30}
    return dict(logit=logit, aft=aft, pooled_km=km_prob(train, horizon), oblast_km=oblast_km, horizon=horizon,
                features=feats)


def predict_m2(fitted: dict, df: pd.DataFrame) -> pd.DataFrame:
    h = fitted["horizon"]
    out = pd.DataFrame(index=df.index)
    out["km_pooled"] = fitted["pooled_km"]
    out["km_oblast"] = df.oblast.map(fitted["oblast_km"]).fillna(fitted["pooled_km"])
    X = df[fitted["features"]].astype(float).fillna(0)
    out["logit"] = fitted["logit"].predict_proba(X)[:, 1]
    surv = fitted["aft"].predict_survival_function(X, times=[h])
    out["weibull_aft"] = 1 - surv.T.iloc[:, 0].to_numpy()
    out["aft_median"] = fitted["aft"].predict_median(X).to_numpy()
    return out


def c_index(df: pd.DataFrame, pred_median: pd.Series) -> float:
    return float(concordance_index(df.remaining, pred_median, df.event))


# ---------- metrics ----------

def scores(y: np.ndarray, p: np.ndarray) -> dict:
    y = np.asarray(y).astype(int)
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return dict(n=len(y), base_rate=y.mean(), brier=brier_score_loss(y, p), log_loss=log_loss(y, p, labels=[0, 1]),
                pr_auc=average_precision_score(y, p) if y.any() else np.nan,
                roc_auc=roc_auc_score(y, p) if 0 < y.mean() < 1 else np.nan)


def table(y, preds: pd.DataFrame, ref: str) -> pd.DataFrame:
    rows = {c: scores(y, preds[c]) for c in preds.columns if c != "aft_median"}
    t = pd.DataFrame(rows).T
    t["bss_vs_" + ref] = 1 - t.brier / t.loc[ref, "brier"]
    return t


def block_bootstrap_diff(y, p_model, p_ref, days, block: int = 3, n: int = 1000, seed: int = 0) -> tuple:
    """Paired bootstrap of BSS = 1 - Brier(model)/Brier(ref), resampling blocks of consecutive days."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(dict(y=np.asarray(y, int), a=p_model, b=p_ref, d=pd.to_datetime(days)))
    udays = np.sort(df.d.unique())
    blocks = [udays[i:i + block] for i in range(0, len(udays), block)]
    by_day = {d: g for d, g in df.groupby("d")}
    out = []
    for _ in range(n):
        pick = rng.integers(0, len(blocks), len(blocks))
        g = pd.concat([by_day[d] for i in pick for d in blocks[i]])
        out.append(1 - ((g.a - g.y) ** 2).mean() / ((g.b - g.y) ** 2).mean())
    est = 1 - ((df.a - df.y) ** 2).mean() / ((df.b - df.y) ** 2).mean()
    return est, float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def reliability(y, p, bins: int = 10) -> pd.DataFrame:
    df = pd.DataFrame(dict(y=np.asarray(y, int), p=p))
    df["bin"] = pd.cut(df.p, np.linspace(0, 1, bins + 1), include_lowest=True)
    return df.groupby("bin", observed=True).agg(n=("y", "size"), mean_p=("p", "mean"), observed=("y", "mean"))


def keep_extra(y, p_base, p_extra, days, block: int = 3, n: int = 500, seed: int = 0) -> tuple[bool, float, float]:
    """1-SE rule from PREREG: keep the richer feature set only if its Brier gain exceeds one bootstrap SE."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(dict(d=pd.to_datetime(days), e=(np.asarray(p_extra) - np.asarray(y, float)) ** 2
                           - (np.asarray(p_base) - np.asarray(y, float)) ** 2))
    per_day = df.groupby("d").e.agg(["sum", "size"])
    udays = per_day.index.to_numpy()
    blocks = [udays[i:i + block] for i in range(0, len(udays), block)]
    diffs = []
    for _ in range(n):
        pick = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        g = per_day.loc[pick]
        diffs.append(g["sum"].sum() / g["size"].sum())
    gain = -float(df.e.mean())
    se = float(np.std(diffs))
    return gain > se, gain, se
