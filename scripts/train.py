"""Prepare the slim DataCo table (if the raw Kaggle CSV is present), train the model, run the backtest."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import RAW_CSV, SLIM_DATA
from src.data import prepare_slim
from src.model import train_model


def main():
    if RAW_CSV.exists():
        print(f"Condensing {RAW_CSV.name} to {SLIM_DATA.name} ...")
        df = prepare_slim()
        print(f"  {len(df):,} orders")
    else:
        print(f"Raw CSV not found; using the committed {SLIM_DATA.name}.")
    print("Training LightGBM late-delivery model + 3-window chronological backtest ...")
    m = train_model()
    for name, key in (("LightGBM", "model"), ("Mode-only baseline", "mode_only_baseline"),
                      ("Overall-rate baseline", "overall_rate_baseline")):
        print(f"  {name:22s} {m['backtest']['mean'][key]}")
    print("Done. Model in models/, metrics in reports/model_metrics.json")


if __name__ == "__main__":
    main()
