"""Reproduce the model tuning (learning rate, tree shape, feature set, decision threshold).

Tuning split: train on the oldest 60% of orders, validate on the next 10% (60-70%), with early
stopping. The backtest test windows are 70-80%, 80-90% and 90-100%, so tuning never sees them.
Takes a few minutes and about 1 GB of RAM.  Run:  python scripts/tune_model.py
"""
import itertools
import sys
from pathlib import Path

import numpy as np
from lightgbm import LGBMClassifier, early_stopping
from sklearn.metrics import accuracy_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.data import load_orders  # noqa: E402
from src.model import CATEGORICAL, PARAMS, _training_frame, make_features  # noqa: E402

TIMING = ["scheduled_days", "store_lat", "store_lon", "dow", "month", "hour"]
FEATURE_SETS = {
    "all (with order size)": CATEGORICAL + TIMING + ["sales", "quantity", "lines"],
    "no order size": CATEGORICAL + TIMING,
    "core 10": ["mode", "scheduled_days", "order_country", "order_region", "market", "customer_state",
                "category", "hour", "dow", "month"],
}


def matrix(frame, cats, feats):
    x = make_features(frame, cats)
    for c in feats:
        if c not in x:
            x[c] = frame[c].astype("float32").to_numpy()
    return x[feats]


def main():
    df = _training_frame(load_orders()).sort_values("order_date").reset_index(drop=True)
    n = len(df)
    tr, va = df.iloc[: int(n * 0.6)], df.iloc[int(n * 0.6): int(n * 0.7)]
    cats = {c: sorted(tr[c].astype(str).unique()) for c in CATEGORICAL}
    results = []
    for (fname, feats), lr, leaves, mcs in itertools.product(
            FEATURE_SETS.items(), [0.01, 0.02, 0.05, 0.1], [7, 15, 31, 63], [40, 150, 400]):
        p = {**PARAMS, "learning_rate": lr, "num_leaves": leaves, "min_child_samples": mcs, "n_estimators": 3000}
        m = LGBMClassifier(**p).fit(matrix(tr, cats, feats), tr.late.astype("float32"),
                                    eval_set=[(matrix(va, cats, feats), va.late.astype("float32"))],
                                    callbacks=[early_stopping(100, verbose=False)])
        pv = m.predict_proba(matrix(va, cats, feats), num_iteration=m.best_iteration_)[:, 1]
        results.append(dict(features=fname, lr=lr, leaves=leaves, min_child=mcs, trees=m.best_iteration_,
                            auc=roc_auc_score(va.late, pv), acc=accuracy_score(va.late, pv >= .5), p=pv))
    results.sort(key=lambda r: -r["auc"])
    print(f"{len(results)} configurations. Top 8 on the validation period:")
    for r in results[:8]:
        print(f"  {r['features']:22s} lr={r['lr']:<5} leaves={r['leaves']:<3} min_child={r['min_child']:<4} "
              f"trees={r['trees']:<5} AUC {r['auc']:.4f} acc {r['acc']:.4f}")
    best = results[0]
    grid = np.arange(0.35, 0.66, 0.01)
    accs = [accuracy_score(va.late, best["p"] >= t) for t in grid]
    print(f"Best decision threshold on validation: {grid[int(np.argmax(accs))]:.2f} "
          f"(accuracy {max(accs):.4f} vs {best['acc']:.4f} at 0.50)")


if __name__ == "__main__":
    main()
