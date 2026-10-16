"""Model chart for the report (docs/images/model_backtest.png).

Left: AUC per chronological test window, LightGBM vs the shipping-mode-only baseline.
Right: the features the model relies on most (LightGBM gain, share of total).
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
METRICS = ROOT / "reports" / "model_metrics.json"
OUT = ROOT / "docs" / "images" / "model_backtest.png"

# Validated categorical slots 1-2 (light surface); text uses ink tokens.
MODEL, BASELINE = "#2a78d6", "#eb6834"
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#1f1f1e", "#5f5e5a", "#e6e5e0"
NICE = {"mode": "shipping mode", "scheduled_days": "promised days", "order_country": "destination country",
        "hour": "order hour", "sales": "order value", "customer_state": "customer state", "category": "product category",
        "store_lat": "store latitude", "store_lon": "store longitude", "order_region": "destination region",
        "month": "month", "dow": "weekday", "department": "department", "quantity": "quantity",
        "market": "market", "payment_type": "payment type", "customer_segment": "customer segment", "lines": "order lines"}


def main() -> None:
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    windows = list(reversed(m["backtest"]["windows"]))  # oldest test block first
    labels = [f"{w['test']['from'][:7]} → {w['test']['to'][:7]}" for w in windows]
    gain = m["feature_importance_gain"]
    total = sum(gain.values())
    top = list(gain.items())[:8]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.6), dpi=200, gridspec_kw={"width_ratios": [1, 1.15]})
    fig.patch.set_facecolor(SURFACE)
    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(GRID)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=INK_2, labelsize=8.5)

    x = range(len(windows))
    ax1.plot(x, [w["mode_only_baseline"]["AUC"] for w in windows], marker="D", ms=7, lw=2, color=BASELINE,
             label="Shipping-mode-only baseline")
    ax1.plot(x, [w["model"]["AUC"] for w in windows], marker="o", ms=8, lw=2, color=MODEL, label="LightGBM")
    for i, w in enumerate(windows):
        ax1.annotate(f"{w['model']['AUC']:.3f}", (i, w["model"]["AUC"]), xytext=(0, 8), textcoords="offset points",
                     ha="center", fontsize=8, color=INK)
    ax1.set_xticks(list(x), labels, fontsize=8)
    ax1.set_ylim(0.70, 0.80)
    ax1.set_ylabel("AUC (higher is better)", color=INK_2, fontsize=9)
    ax1.grid(axis="y", color=GRID, lw=0.8)
    ax1.set_axisbelow(True)
    ax1.set_title("Late-delivery AUC per test window", loc="left", fontsize=10.5, color=INK, fontweight="bold")
    h, lab = ax1.get_legend_handles_labels()
    ax1.legend(h[::-1], lab[::-1], frameon=False, fontsize=8.5, loc="lower right", labelcolor=INK)

    names = [NICE.get(f, f) for f, _ in top][::-1]
    shares = [g / total * 100 for _, g in top][::-1]
    ax2.barh(names, shares, color=MODEL, height=0.6)
    for i, v in enumerate(shares):
        ax2.text(v + 0.8, i, f"{v:.0f}%", va="center", fontsize=8, color=INK)
    ax2.set_xlim(0, max(shares) * 1.18)
    ax2.set_xlabel("share of total split gain (%)", color=INK_2, fontsize=9)
    ax2.tick_params(axis="y", colors=INK, length=0)
    ax2.grid(axis="x", color=GRID, lw=0.8)
    ax2.set_axisbelow(True)
    ax2.set_title("What the model relies on", loc="left", fontsize=10.5, color=INK, fontweight="bold")

    fig.text(0.01, 0.985, "Late-delivery risk model: chronological backtest on DataCo orders",
             fontsize=13, fontweight="bold", color=INK, va="top")
    fig.text(0.01, 0.925, "Each window trains only on earlier orders. Shipping mode dominates DataCo lateness, so the "
             "mode-only baseline is strong; the model adds lane, timing and order features.",
             fontsize=8.8, color=INK_2, va="top")
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(OUT, facecolor=SURFACE)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
