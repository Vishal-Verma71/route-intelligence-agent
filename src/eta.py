"""ETA, SLA and cost evaluation for every option a shipment has.

Truck shipments: each OSRM route from the truck's current position to the destination is
evaluated with live weather (speed penalty), active incidents near the route (closures block
it, congestion adds hours, storms slow it) and hours-of-service rest stops. International
shipments: air transit + customs clearance (plus any active hold) + last-mile delivery,
for each customs option.

Cost = fuel + driver wages (overtime after 8 h/day) + option surcharge + SLA penalty for
every day past the promised delivery time. The agent recommends the cheapest total cost.
Each option carries a breakdown (where its hours and dollars come from), and `compare`
puts any alternative side by side with the current plan.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from . import config, customs, db
from .data import haversine_km
from .routing import min_distance_km, route_options
from .weather import point_weather, route_weather_many

UTC_FMT = "%Y-%m-%dT%H:%M"


def _fmt(dt: datetime) -> str:
    return dt.strftime(UTC_FMT)


def parse(ts: str) -> datetime:
    return datetime.strptime(ts[:16], UTC_FMT)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)


def elapsed_with_rest(drive_h: float) -> tuple[float, int]:
    """Wall-clock hours for a drive, adding a rest break after every 11 h behind the wheel."""
    rests = max(0, math.ceil(drive_h / config.MAX_DRIVE_H_PER_DAY) - 1)
    return drive_h + rests * config.REST_H, rests


def driver_hours(drive_h: float) -> tuple[float, float]:
    """Regular and overtime hours: driving spread evenly over 11-hour days, overtime after 8 h a day."""
    days = max(1, math.ceil(drive_h / config.MAX_DRIVE_H_PER_DAY))
    regular = min(drive_h / days, config.OVERTIME_AFTER_H) * days
    return regular, max(0.0, drive_h - regular)


def driver_cost(drive_h: float) -> float:
    regular, overtime = driver_hours(drive_h)
    return regular * config.DRIVER_WAGE_PER_H + overtime * config.DRIVER_WAGE_PER_H * config.OVERTIME_MULTIPLIER


def sla_penalty(late_h: float, order_value: float) -> float:
    if late_h <= 0:
        return 0.0
    days = math.ceil(late_h / 24)
    return days * max(config.SLA_PENALTY_MIN, config.SLA_PENALTY_PCT_PER_DAY * order_value)


def _incident_effects(geometry, incidents) -> dict:
    blocked, delay_h, slow, hits = False, 0.0, 0.0, []
    for inc in incidents:
        if inc["kind"] == "customs_hold":
            continue
        d = min_distance_km(geometry, (inc["lat"], inc["lon"]))
        if d > inc["radius_km"]:
            continue
        hits.append({"id": inc["id"], "kind": inc["kind"], "title": inc["title"], "distance_km": round(d, 1)})
        if inc["kind"] == "road_closure":
            blocked = True
        delay_h += inc["delay_h"] or 0.0
        slow = max(slow, inc["speed_penalty"] or 0.0)
    return {"blocked": blocked, "delay_h": delay_h, "speed_penalty": slow, "incidents": hits}


def _sla(eta: datetime, promised: datetime) -> dict:
    slack_h = (promised - eta).total_seconds() / 3600
    status = "ON_TIME" if slack_h >= 4 else "AT_RISK" if slack_h >= 0 else "BREACH"
    return {"status": status, "slack_h": round(slack_h, 1), "late_h": round(max(0.0, -slack_h), 1)}


def truck_options(s: dict, incidents: list[dict] | None = None, with_weather: bool = True) -> list[dict]:
    incidents = db.get_incidents() if incidents is None else incidents
    start = now()
    promised = parse(s["promised_at"])
    out = []
    routes = route_options((s["current_lat"], s["current_lon"]), (s["dest_lat"], s["dest_lon"]))
    no_weather = {"available": False, "penalty": 0.0, "conditions": []}
    weather = route_weather_many([r["geometry"] for r in routes]) if with_weather else [no_weather] * len(routes)
    for r, w in zip(routes, weather):
        inc = _incident_effects(r["geometry"], incidents)
        penalty = max(w["penalty"], inc["speed_penalty"])
        drive_h = r["duration_h"] * (1 + penalty) + inc["delay_h"]
        if inc["blocked"]:
            drive_h += 72.0  # a closure strands the truck until it is lifted or the truck detours
        wall_h, rests = elapsed_with_rest(drive_h)
        eta = start + timedelta(hours=wall_h)
        sla = _sla(eta, promised)
        fuel_l = r["distance_km"] * config.FUEL_L_PER_100KM / 100
        fuel = fuel_l * config.FUEL_PRICE_PER_L
        regular_h, overtime_h = driver_hours(drive_h)
        driver = driver_cost(drive_h)
        penalty_cost = sla_penalty(sla["late_h"], s["order_value"])
        slow_h = r["duration_h"] * penalty  # weather or storm slowdown, whichever is worse
        breakdown = {
            "free_flow_h": round(r["duration_h"], 1),
            "weather_h": round(slow_h, 1) if w["penalty"] >= inc["speed_penalty"] else 0.0,
            "storm_h": round(slow_h, 1) if inc["speed_penalty"] > w["penalty"] else 0.0,
            "congestion_h": round(inc["delay_h"], 1),
            "closure_h": 72.0 if inc["blocked"] else 0.0,
            "rest_h": round(rests * config.REST_H, 1),
            "trip_h": round(wall_h, 1),
            "avg_speed_kmh": round(r["distance_km"] / drive_h, 1) if drive_h else None,
            "fuel_l": round(fuel_l), "co2_kg": round(fuel_l * config.CO2_KG_PER_L),
            "regular_h": round(regular_h, 1), "overtime_h": round(overtime_h, 1),
            "late_days": math.ceil(sla["late_h"] / 24) if sla["late_h"] > 0 else 0,
        }
        out.append({
            "option": r["route_id"], "label": f"Route {r['route_id']} ({r['via']}, {r['distance_km']:,.0f} km)",
            "distance_km": r["distance_km"], "free_flow_h": r["duration_h"], "drive_h": round(drive_h, 1),
            "rest_stops": rests, "eta": _fmt(eta), "sla": sla, "blocked": inc["blocked"],
            "weather": {"available": w["available"], "penalty": w["penalty"],
                        "worst": w["conditions"][0]["condition"] if w["conditions"] else None},
            "incidents": inc["incidents"], "breakdown": breakdown,
            "cost": {"fuel": round(fuel, 2), "driver": round(driver, 2), "sla_penalty": round(penalty_cost, 2),
                     "surcharge": 0.0, "total": round(fuel + driver + penalty_cost, 2)},
            "geometry": r["geometry"], "source": r["source"],
        })
    return out


def intl_options(s: dict, incidents: list[dict] | None = None, with_weather: bool = True) -> list[dict]:
    incidents = db.get_incidents() if incidents is None else incidents
    start = now()
    promised = parse(s["promised_at"])
    dist = float(haversine_km(s["current_lat"], s["current_lon"], s["dest_lat"], s["dest_lon"]))
    hold = sum(i["delay_h"] or 0 for i in incidents if i["kind"] == "customs_hold" and i["country"] == s["dest_country"])
    w = point_weather(s["dest_lat"], s["dest_lon"]) if with_weather else {"available": False, "penalty": 0.0, "conditions": []}
    base_clear = customs.clearance_hours(s["dest_country"])
    out = []
    for key, opt in customs.OPTIONS.items():
        air_h = dist / opt["air_kmh"] * (1 + w["penalty"])
        clear_h = (base_clear + hold) * opt["clearance_factor"]
        left = 1 - s["progress"]
        total_h = air_h * left + opt["handling_h"] * left + clear_h + customs.LAST_MILE_H
        eta = start + timedelta(hours=total_h)
        sla = _sla(eta, promised)
        penalty_cost = sla_penalty(sla["late_h"], s["order_value"])
        freight = 2.2 * dist / 100  # flat illustrative air-freight share for this order
        out.append({
            "option": key, "label": opt["label"], "distance_km": round(dist, 0), "drive_h": None,
            "transit_h": round(total_h, 1), "customs_h": round(clear_h, 1), "customs_hold_h": hold,
            "eta": _fmt(eta), "sla": sla, "blocked": False,
            "breakdown": {"air_h": round(air_h * left, 1), "handling_h": round(opt["handling_h"] * left, 1),
                          "customs_h": round(clear_h, 1), "last_mile_h": customs.LAST_MILE_H,
                          "trip_h": round(total_h, 1),
                          "late_days": math.ceil(sla["late_h"] / 24) if sla["late_h"] > 0 else 0},
            "weather": {"available": w["available"], "penalty": w["penalty"],
                        "worst": w["conditions"][0]["condition"] if w["conditions"] else None},
            "incidents": [{"kind": "customs_hold", "title": i["title"], "id": i["id"]} for i in incidents
                          if i["kind"] == "customs_hold" and i["country"] == s["dest_country"]],
            "cost": {"freight": round(freight, 2), "surcharge": opt["extra_cost"], "sla_penalty": round(penalty_cost, 2),
                     "total": round(freight + opt["extra_cost"] + penalty_cost, 2)},
            "geometry": [[s["current_lat"], s["current_lon"]], [s["dest_lat"], s["dest_lon"]]],
            "source": "air transit estimate + customs model",
        })
    return out


def compare(cur: dict, alt: dict) -> dict:
    """What switching from `cur` to `alt` changes: positive saving = money saved, negative hours = sooner."""
    def delta(a, b):
        return None if a is None or b is None else round(b - a, 1)

    cb, ab = cur["breakdown"], alt["breakdown"]
    saving = {k: round(cur["cost"].get(k, 0) - alt["cost"].get(k, 0), 2)
              for k in ("fuel", "driver", "freight", "surcharge", "sla_penalty", "total")
              if k in cur["cost"] or k in alt["cost"]}
    return {
        "from": cur["option"], "to": alt["option"], "saving": saving,
        "distance_km": delta(cur["distance_km"], alt["distance_km"]),
        "drive_h": delta(cur.get("drive_h"), alt.get("drive_h")),
        "trip_h": delta(cb["trip_h"], ab["trip_h"]),
        "rest_stops": None if cur.get("rest_stops") is None else alt["rest_stops"] - cur["rest_stops"],
        "overtime_h": delta(cb.get("overtime_h"), ab.get("overtime_h")),
        "fuel_l": delta(cb.get("fuel_l"), ab.get("fuel_l")),
        "co2_kg": delta(cb.get("co2_kg"), ab.get("co2_kg")),
        "customs_h": delta(cur.get("customs_h"), alt.get("customs_h")),
        "eta_change_h": round((parse(alt["eta"]) - parse(cur["eta"])).total_seconds() / 3600, 1),
        "sla": {"from": cur["sla"]["status"], "to": alt["sla"]["status"],
                "late_h_from": cur["sla"]["late_h"], "late_h_to": alt["sla"]["late_h"]},
        "disruptions_avoided": sorted({i["kind"] for i in cur["incidents"]} - {i["kind"] for i in alt["incidents"]}),
    }


def evaluate(s: dict, incidents: list[dict] | None = None, with_weather: bool = True) -> dict:
    """All options for a shipment, the current one, and the cheapest-total-cost recommendation."""
    opts = (truck_options if s["kind"] == "truck" else intl_options)(s, incidents, with_weather)
    current = next((o for o in opts if o["option"] == s["active_option"]), opts[0])
    best = min(opts, key=lambda o: (o["cost"]["total"], o["sla"]["late_h"]))
    saving = round(current["cost"]["total"] - best["cost"]["total"], 2)
    reroute = best["option"] != current["option"] and saving > 1.0
    return {"shipment_id": s["id"], "kind": s["kind"], "current": current, "best": best,
            "recommend_change": reroute, "saving": saving if reroute else 0.0, "options": opts,
            "comparisons": {o["option"]: compare(current, o) for o in opts if o["option"] != current["option"]},
            "assumptions": ASSUMPTIONS}


# Shown next to the numbers in the UI, so every figure can be traced to its inputs.
ASSUMPTIONS = {
    "fuel_l_per_100km": config.FUEL_L_PER_100KM, "fuel_price_per_l": config.FUEL_PRICE_PER_L,
    "co2_kg_per_l": config.CO2_KG_PER_L, "driver_wage_per_h": config.DRIVER_WAGE_PER_H,
    "overtime_after_h": config.OVERTIME_AFTER_H, "overtime_multiplier": config.OVERTIME_MULTIPLIER,
    "max_drive_h_per_day": config.MAX_DRIVE_H_PER_DAY, "rest_h": config.REST_H,
    "sla_penalty_pct_per_day": config.SLA_PENALTY_PCT_PER_DAY, "sla_penalty_min": config.SLA_PENALTY_MIN,
    "closure_stranded_h": 72.0, "at_risk_slack_h": 4.0,
}
