"""Auto-generated Markdown operations report: fleet status, disruptions, decisions, model quality."""
from __future__ import annotations

from datetime import datetime, timezone

from . import db
from .data import dataset_summary
from .model import load_metrics
from .tools import evaluate_fleet


def build_report() -> str:
    rows = evaluate_fleet()
    L = ["# Route Intelligence — Operations Report", "",
         f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC", ""]
    counts = {k: sum(r["sla"] == k for r in rows) for k in ("BREACH", "AT_RISK", "ON_TIME")}
    L += ["## 1. Fleet status",
          f"{len(rows)} active shipments ({sum(r['kind'] == 'truck' for r in rows)} trucks, "
          f"{sum(r['kind'] == 'international' for r in rows)} international). "
          f"Predicted SLA breaches: **{counts['BREACH']}**, at risk: {counts['AT_RISK']}, on time: {counts['ON_TIME']}.", ""]
    flagged = [r for r in rows if r["sla"] != "ON_TIME" or r["recommend_change"]]
    L += ["## 2. Shipments needing attention", "| Shipment | Destination | SLA | Slack (h) | Cause | Better option | Saving |",
          "|---|---|---|---:|---|---|---:|"]
    for r in sorted(flagged, key=lambda r: r["slack_h"]):
        cause = ", ".join(i.replace("_", " ") for i in r["incidents"]) or (r["weather"] or "-")
        better = f"{r['best_option']} ({r['best_sla']})" if r["recommend_change"] else "-"
        L.append(f"| {r['id']} | {r['dest']} | {r['sla']} | {r['slack_h']} | {cause} | {better} | ${r['saving']:,.0f} |")
    L += ["", "## 3. Active disruptions"]
    for i in db.get_incidents():
        L.append(f"- #{i['id']} {i['kind'].replace('_', ' ')}: {i['title']} (source: {i['source']})")
    L += ["", "## 4. Reroute decisions (agent proposes, dispatcher approves)"]
    decisions = db.get_decisions()
    L += [f"- #{d['id']} {d['shipment_id']}: {d['from_option']} → {d['to_option']} [{d['status']}] "
          f"SLA {d['sla_before']} → {d['sla_after']}, cost ${d['cost_before']:,.0f} → ${d['cost_after']:,.0f}"
          for d in decisions] or ["- none yet"]
    L += ["", "## 5. Customer delay notices (drafts)"]
    L += [f"- {n['shipment_id']}: {n['message']}" for n in db.get_notifications()] or ["- none yet"]
    m = load_metrics()
    if m:
        mean = m["backtest"]["mean"]
        L += ["", "## 6. Late-delivery model (LightGBM)",
              f"Chronological backtest, {len(m['backtest']['windows'])} windows of ~6,300 orders each.", "",
              "| Model | AUC | Accuracy | Brier |", "|---|---:|---:|---:|"]
        for name, key in (("LightGBM", "model"), ("Shipping-mode-only baseline", "mode_only_baseline"),
                          ("Overall late rate", "overall_rate_baseline")):
            L.append(f"| {name} | {mean[key]['AUC']} | {mean[key]['accuracy']} | {mean[key]['brier']} |")
    d = dataset_summary()
    L += ["", "## 7. Data",
          f"{d['source']}: {d['orders']:,} orders, {d['date_from']} to {d['date_to']}, late rate {d['late_rate']:.1%}.",
          "Today's fleet replays the most recent DataCo orders as if in transit now; routes (OSRM), weather "
          "(Open-Meteo) and geocoding are live. Incidents are a seeded scenario plus anything the dispatcher reports."]
    return "\n".join(L)
