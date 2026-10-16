"""Agent tools (OpenAI function-calling schemas) and their dispatcher."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from . import customs, db, eta
from .data import country_en, dataset_summary
from .geo import geocode
from .model import load_metrics
from .weather import route_weather

SHIP_ID = re.compile(r"\b(TRK|INT)[- ]?(\d{1,3})\b", re.IGNORECASE)
INCIDENT_KINDS = ("road_closure", "congestion", "storm", "customs_hold")


def norm_id(text: str) -> str | None:
    m = SHIP_ID.search(text or "")
    return f"{m.group(1).upper()}-{int(m.group(2)):03d}" if m else None


def _slim(option: dict) -> dict:
    """Option without the bulky polyline, for the agent's context."""
    return {k: v for k, v in option.items() if k != "geometry"}


def _shipment_or_error(shipment_id: str) -> dict:
    s = db.get_shipment(norm_id(shipment_id) or shipment_id)
    if not s:
        raise KeyError(f"Unknown shipment {shipment_id}")
    return s


def evaluate_fleet() -> list[dict]:
    ships = db.get_shipments()
    incidents = db.get_incidents()
    with ThreadPoolExecutor(max_workers=8) as ex:  # I/O bound: routing + weather calls
        evals = list(ex.map(lambda s: eta.evaluate(s, incidents), ships))
    out = []
    for s, e in zip(ships, evals):
        cur = e["current"]
        out.append({"id": s["id"], "kind": s["kind"], "dest": f"{s['dest_city']}, {country_en(s['dest_country'])}",
                    "mode": s["mode"], "late_prob": s["late_prob"], "sla": cur["sla"]["status"],
                    "slack_h": cur["sla"]["slack_h"], "eta": cur["eta"], "promised_at": s["promised_at"],
                    "cost_total": cur["cost"]["total"], "incidents": [i["kind"] for i in cur["incidents"]],
                    "weather": cur["weather"]["worst"], "recommend_change": e["recommend_change"],
                    "best_option": e["best"]["option"], "saving": e["saving"],
                    "best_sla": e["best"]["sla"]["status"]})
    return out


def tool_scan_fleet(args: dict) -> dict:
    rows = evaluate_fleet()
    order = {"BREACH": 0, "AT_RISK": 1, "ON_TIME": 2}
    at_risk = sorted([r for r in rows if r["sla"] != "ON_TIME" or r["recommend_change"]],
                     key=lambda r: (order[r["sla"]], -r["saving"]))
    counts = {k: sum(r["sla"] == k for r in rows) for k in order}
    return {"shipments": len(rows), "sla_counts": counts, "reroute_opportunities": sum(r["recommend_change"] for r in rows),
            "potential_saving": round(sum(r["saving"] for r in rows), 2), "at_risk": at_risk,
            "active_incidents": len(db.get_incidents())}


def tool_get_shipment(args: dict) -> dict:
    s = _shipment_or_error(args["shipment_id"])
    return {k: v for k, v in s.items() if k != "features"}


def tool_predict_delay_risk(args: dict) -> dict:
    s = _shipment_or_error(args["shipment_id"])
    m = load_metrics() or {}
    mode_rate = dataset_summary()["late_rate_by_mode"].get(s["mode"])
    return {"shipment_id": s["id"], "late_probability": s["late_prob"], "mode": s["mode"],
            "historical_late_rate_for_mode": mode_rate,
            "model": m.get("model"), "backtest_auc": (m.get("backtest") or {}).get("mean", {}).get("model", {}).get("AUC"),
            "meaning": "historical risk of this lane/service level before live disruptions; DataCo lateness is driven mostly by shipping mode"}


def tool_check_weather(args: dict) -> dict:
    s = _shipment_or_error(args["shipment_id"])
    e = eta.evaluate(s)
    detail = route_weather(e["current"]["geometry"])  # full hourly findings (cached from the evaluation)
    return {"shipment_id": s["id"], "route": e["current"]["label"], **detail,
            "all_options": {o["option"]: o["weather"] for o in e["options"]}}


def tool_check_incidents(args: dict) -> dict:
    sid = args.get("shipment_id")
    if not sid:
        return {"incidents": db.get_incidents()}
    s = _shipment_or_error(sid)
    e = eta.evaluate(s)
    return {"shipment_id": s["id"], "on_current_route": e["current"]["incidents"],
            "per_option": {o["option"]: o["incidents"] for o in e["options"]}}


def tool_check_customs(args: dict) -> dict:
    s = _shipment_or_error(args["shipment_id"])
    if s["kind"] != "international":
        return {"shipment_id": s["id"], "applies": False, "note": "domestic truck shipment, no customs clearance"}
    holds = [i for i in db.get_incidents() if i["kind"] == "customs_hold" and i["country"] == s["dest_country"]]
    return {"shipment_id": s["id"], "applies": True, "destination": country_en(s["dest_country"]),
            "typical_clearance_h": customs.clearance_hours(s["dest_country"]),
            "active_holds": [{"title": h["title"], "extra_h": h["delay_h"]} for h in holds],
            "options": {k: v["label"] for k, v in customs.OPTIONS.items()}}


def tool_route_options(args: dict) -> dict:
    s = _shipment_or_error(args["shipment_id"])
    e = eta.evaluate(s)
    return {"shipment_id": s["id"], "promised_at": s["promised_at"], "current_option": s["active_option"],
            "options": [_slim(o) for o in e["options"]], "recommended": e["best"]["option"],
            "recommend_change": e["recommend_change"], "saving": e["saving"],
            "comparison_with_recommended": e["comparisons"].get(e["best"]["option"])}


def tool_propose_reroute(args: dict) -> dict:
    """Create a PENDING reroute decision for the dispatcher (idempotent)."""
    s = _shipment_or_error(args["shipment_id"])
    e = eta.evaluate(s)
    target = args.get("option") or e["best"]["option"]
    to = next((o for o in e["options"] if o["option"] == target), None)
    if to is None:
        raise ValueError(f"Option {target} does not exist for {s['id']}")
    cur = e["current"]
    if to["option"] == cur["option"]:
        return {"shipment_id": s["id"], "status": "NO_CHANGE", "note": "current option is already the best"}
    why = []
    if cur["blocked"]:
        why.append("current route is blocked by a road closure")
    other = [i["kind"].replace("_", " ") for i in cur["incidents"] if i["kind"] != "road_closure"]
    if other:
        why.append(("customs hold at destination" if other == ["customs hold"] else "on current route: " + ", ".join(other)))
    if cur["weather"]["worst"]:
        why.append(f"weather on current route: {cur['weather']['worst']}")
    status = lambda st: st.replace("_", " ").lower()
    why.append(f"{status(cur['sla']['status'])} → {status(to['sla']['status'])}, total cost "
               f"${cur['cost']['total']:,.0f} → ${to['cost']['total']:,.0f}")
    did, created = db.add_decision({
        "shipment_id": s["id"], "from_option": cur["option"], "to_option": to["option"],
        "eta_before": cur["eta"], "eta_after": to["eta"], "cost_before": cur["cost"]["total"],
        "cost_after": to["cost"]["total"], "sla_before": cur["sla"]["status"], "sla_after": to["sla"]["status"],
        "reason": "; ".join(why), "decision_key": f"{s['id']}:{cur['option']}->{to['option']}"})
    return {"shipment_id": s["id"], "decision_id": did, "status": "PENDING approval" if created else "already pending",
            "from": cur["option"], "to": to["option"], "label": to["label"], "eta_after": to["eta"],
            "sla_after": to["sla"]["status"], "saving": round(cur["cost"]["total"] - to["cost"]["total"], 2),
            "comparison": e["comparisons"][to["option"]], "reason": "; ".join(why)}


def tool_draft_customer_notice(args: dict) -> dict:
    s = _shipment_or_error(args["shipment_id"])
    e = eta.evaluate(s)
    plan = e["best"] if e["recommend_change"] else e["current"]
    if plan["sla"]["status"] == "ON_TIME":
        return {"shipment_id": s["id"], "drafted": False, "note": "shipment is on time; no notice needed"}
    msg = (f"Order {s['order_id']} to {s['dest_city']}: because of "
           f"{', '.join(i['kind'].replace('_', ' ') for i in plan['incidents']) or 'conditions on the route'}, "
           f"your delivery is now expected on {plan['eta'].replace('T', ' ')} UTC "
           f"(promised {s['promised_at'].replace('T', ' ')} UTC). We are sorry for the delay.")
    nid = db.add_notification(s["id"], "email", msg)
    return {"shipment_id": s["id"], "drafted": True, "notification_id": nid, "message": msg, "status": "DRAFT"}


def tool_report_disruption(args: dict) -> dict:
    """Log a disruption the dispatcher reports (e.g. 'storm near Denver'); geocoded via Open-Meteo."""
    kind = args["kind"]
    if kind not in INCIDENT_KINDS:
        raise ValueError(f"kind must be one of {INCIDENT_KINDS}")
    if kind == "customs_hold":
        country = args["country"]
        iid = db.add_incident(kind, args.get("title") or f"Customs hold reported in {country}", 0, 0, 0,
                              float(args.get("delay_h", 48)), 0, country, "dispatcher")
        return {"incident_id": iid, "kind": kind, "country": country}
    place = geocode(args["city"], args.get("country", "Estados Unidos"), args.get("state"))
    if not place:
        raise ValueError(f"Could not locate {args['city']}")
    defaults = {"road_closure": (15, 0, 0), "congestion": (25, 4, 0), "storm": (80, 0, 0.3)}
    radius, delay, slow = defaults[kind]
    iid = db.add_incident(kind, args.get("title") or f"{kind.replace('_', ' ').title()} near {place.get('name_en', args['city'])}",
                          place["lat"], place["lon"], float(args.get("radius_km", radius)),
                          float(args.get("delay_h", delay)), float(args.get("speed_penalty", slow)), None, "dispatcher")
    return {"incident_id": iid, "kind": kind, "near": place.get("name_en"), "lat": place["lat"], "lon": place["lon"]}


def _fn(name, desc, props=None, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": list(required)}}}


_SID = {"shipment_id": {"type": "string", "description": "e.g. TRK-001 or INT-004"}}

OPENAI_TOOLS = [
    _fn("scan_fleet", "Evaluate every active shipment (live weather, incidents, customs, routes) and list those at risk of SLA breach or with a cheaper option."),
    _fn("get_shipment", "Shipment details: origin, destination, mode, promised time, progress.", _SID, ["shipment_id"]),
    _fn("predict_delay_risk", "ML (LightGBM) probability that this shipment is late, from DataCo history.", _SID, ["shipment_id"]),
    _fn("check_weather", "Live Open-Meteo weather along the shipment's route(s) and the speed penalty.", _SID, ["shipment_id"]),
    _fn("check_incidents", "Traffic incidents (closures, congestion, storms) on the shipment's routes, or all active incidents.", _SID),
    _fn("check_customs", "Customs clearance time and active holds for an international shipment.", _SID, ["shipment_id"]),
    _fn("get_route_options", "All route/customs options with recalculated ETA, SLA status and total cost.", _SID, ["shipment_id"]),
    _fn("propose_reroute", "Create a reroute proposal for dispatcher approval (defaults to the cheapest option).",
        {**_SID, "option": {"type": "string", "description": "option id, e.g. R2 or expedited_broker"}}, ["shipment_id"]),
    _fn("draft_customer_notice", "Draft a delay notice to the customer with the new ETA (not sent).", _SID, ["shipment_id"]),
    _fn("report_disruption", "Log a disruption reported by the user (road_closure, congestion, storm near a city; or customs_hold in a country).",
        {"kind": {"type": "string", "enum": list(INCIDENT_KINDS)}, "city": {"type": "string"},
         "state": {"type": "string"}, "country": {"type": "string", "description": "Spanish DataCo name, e.g. Estados Unidos"},
         "delay_h": {"type": "number"}}, ["kind"]),
]

_DISPATCH = {
    "scan_fleet": tool_scan_fleet, "get_shipment": tool_get_shipment, "predict_delay_risk": tool_predict_delay_risk,
    "check_weather": tool_check_weather, "check_incidents": tool_check_incidents, "check_customs": tool_check_customs,
    "get_route_options": tool_route_options, "propose_reroute": tool_propose_reroute,
    "draft_customer_notice": tool_draft_customer_notice, "report_disruption": tool_report_disruption,
}


def execute_tool(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"tool": name, "error": "unknown tool"}
    try:
        return {"tool": name, **fn(args or {})}
    except Exception as e:
        return {"tool": name, "error": f"{type(e).__name__}: {e}"}
