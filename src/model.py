"""Late-delivery risk model: LightGBM classifier on DataCo orders (the agent's ML tool).

Predicts the probability that a shipment arrives after its scheduled delivery date, using
only what is known at dispatch: shipping mode, promised days, lane (store location,
destination market/region/country), product, customer segment and order timing. Delivery
status, real shipping days and order status are excluded because they leak the answer.

Evaluated with a chronological backtest against a "shipping mode only" baseline. DataCo's
lateness is driven mostly by shipping mode, so the baseline is strong; the report says so.

Tuning (scripts/tune_model.py) used only a validation period that no test window overlaps:
train on the oldest 60% of orders, validate on the next 10%. It searched learning rate, tree
shape and feature sets, picked lr 0.1 with early-stopped trees, dropped the order-size
columns (pure noise for lateness), and chose the 0.57 decision threshold. The final model
averages five seeds of the same LightGBM setup for steadier probabilities.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from functools import lru_cache

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

from .config import MODEL_DIR, RANDOM_SEED, REPORT_DIR
from .data import load_orders

MODEL_NAME = "LightGBM (LGBMClassifier) late-delivery risk"
MODEL_PATH = MODEL_DIR / "late_risk_lgbm.joblib"
METRICS_PATH = REPORT_DIR / "model_metrics.json"

CATEGORICAL = ["mode", "market", "order_region", "order_country", "customer_segment", "customer_state",
               "department", "category", "payment_type"]
NUMERIC = ["scheduled_days", "store_lat", "store_lon", "dow", "month", "hour"]
FEATURES = CATEGORICAL + NUMERIC

# Tuned on the validation period: lr 0.1 early-stopped at 34 trees (+10% when refitting on more data).
PARAMS = dict(n_estimators=37, learning_rate=0.1, num_leaves=63, min_child_samples=40,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.9, reg_lambda=1.0,
              random_state=RANDOM_SEED, verbose=-1)
SEEDS = [RANDOM_SEED, 7, 13, 21, 99]  # seed-averaged ensemble
THRESHOLD = 0.57  # "late" decision cut-off, chosen on the validation period

_LOCK = threading.Lock()


def _training_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Delivered orders only (cancelled shipments never arrive), with dispatch-time features."""
    out = df[df["delivery_status"] != "Shipping canceled"].copy()
    out["dow"] = out["order_date"].dt.dayofweek.astype("int8")
    out["month"] = out["order_date"].dt.month.astype("int8")
    out["hour"] = out["order_date"].dt.hour.astype("int8")
    return out


def make_features(rows: pd.DataFrame, categories: dict[str, list]) -> pd.DataFrame:
    """Build the model matrix, mapping categories onto the training vocabulary."""
    x = rows.copy()
    for c in ("dow", "month", "hour"):
        if c not in x:
            src = pd.to_datetime(x["order_date"])
            x[c] = getattr(src.dt, {"dow": "dayofweek", "month": "month", "hour": "hour"}[c])
    for c in CATEGORICAL:
        x[c] = pd.Categorical(x[c].astype(str), categories=categories[c])
    for c in NUMERIC:
        x[c] = pd.to_numeric(x[c], errors="coerce").astype("float32")
    return x[FEATURES]


def _fit(train: pd.DataFrame) -> tuple[list[LGBMClassifier], dict[str, list]]:
    categories = {c: sorted(train[c].astype(str).unique().tolist()) for c in CATEGORICAL}
    x, y = make_features(train, categories), train["late"].astype("float32")
    models = [LGBMClassifier(**{**PARAMS, "random_state": s}).fit(x, y) for s in SEEDS]
    return models, categories


def _predict(models: list[LGBMClassifier], x: pd.DataFrame, **kw) -> np.ndarray:
    return np.mean([m.predict_proba(x, **kw)[:, 1] for m in models], axis=0)


def _scores(y, p, threshold: float = 0.5) -> dict:
    return {"AUC": round(float(roc_auc_score(y, p)), 4),
            "accuracy": round(float(accuracy_score(y, p >= threshold)), 4),
            "brier": round(float(brier_score_loss(y, p)), 4)}


def backtest(windows: int = 3, test_frac: float = 0.10) -> dict:
    """Rolling-origin chronological evaluation: train on the past, test on the next block."""
    df = _training_frame(load_orders()).sort_values("order_date").reset_index(drop=True)
    n = len(df)
    results = []
    for w in range(windows):
        end = n - w * int(n * test_frac)
        start = end - int(n * test_frac)
        train, test = df.iloc[:start], df.iloc[start:end]
        models, cats = _fit(train)
        p_model = _predict(models, make_features(test, cats))
        mode_rate = train.groupby("mode", observed=True)["late"].mean()
        p_mode = test["mode"].map(mode_rate).astype(float).fillna(train["late"].mean()).to_numpy()
        p_prior = np.full(len(test), train["late"].mean())
        results.append({
            "window": w + 1,
            "train": {"from": str(train["order_date"].min().date()), "to": str(train["order_date"].max().date()),
                      "orders": int(len(train))},
            "test": {"from": str(test["order_date"].min().date()), "to": str(test["order_date"].max().date()),
                     "orders": int(len(test))},
            "model": _scores(test["late"], p_model, THRESHOLD),
            "mode_only_baseline": _scores(test["late"], p_mode),
            "overall_rate_baseline": _scores(test["late"], p_prior),
        })
    mean = {k: {m: round(float(np.mean([r[k][m] for r in results])), 4) for m in ("AUC", "accuracy", "brier")}
            for k in ("model", "mode_only_baseline", "overall_rate_baseline")}
    return {"windows": results, "mean": mean}


def train_model() -> dict:
    """Backtest, then fit the production model on all delivered orders and save both."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    evaluation = backtest()
    df = _training_frame(load_orders())
    models, cats = _fit(df)
    gain = np.mean([m.booster_.feature_importance("gain") for m in models], axis=0)
    importance = sorted(zip(FEATURES, gain), key=lambda t: -t[1])
    metrics = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL_NAME,
        "target": "late = delivered after the scheduled number of days (DataCo Late_delivery_risk)",
        "features": FEATURES,
        "excluded_as_leaky": ["delivery_status", "real_days", "order_status", "shipping_date"],
        "training_orders": int(len(df)),
        "backtest": evaluation,
        "feature_importance_gain": {f: round(float(g), 1) for f, g in importance},
        "hyperparameters": {k: v for k, v in PARAMS.items() if k != "verbose"},
        "ensemble_seeds": SEEDS,
        "decision_threshold": THRESHOLD,
        "tuning": "lr, tree shape and features chosen on a validation period (orders 60-70%) that no test window overlaps",
    }
    joblib.dump({"models": models, "categories": cats, "name": MODEL_NAME, "threshold": THRESHOLD}, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    _bundle.cache_clear()
    return metrics


@lru_cache(maxsize=1)
def _bundle() -> dict:
    # A fresh clone has no trained model (models/ is gitignored): train once on first use.
    with _LOCK:
        if not MODEL_PATH.exists():
            train_model()
    return joblib.load(MODEL_PATH)


def predict_late_proba(rows: pd.DataFrame) -> np.ndarray:
    """Probability that each shipment is late, from dispatch-time features."""
    b = _bundle()
    if "models" not in b:  # model saved by an older version: retrain once
        with _LOCK:
            train_model()
        b = _bundle()
    x = make_features(rows, b["categories"])
    with _LOCK:  # LightGBM predictions from several API threads at once can contend
        return _predict(b["models"], x, num_threads=1)


def load_metrics() -> dict | None:
    if METRICS_PATH.exists():
        return json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    return None


def model_ready() -> bool:
    return MODEL_PATH.exists()
