"""Build today's active fleet from the most recent DataCo orders, plus a disruption scenario.

DataCo ends in January 2018, so the fleet is a *replay*: real recent orders (store
coordinates, destination city, shipping mode, promised days, order value) re-dated so they
are in transit right now. Trucks run on real OSRM road routes from mainland US stores to US
and Canadian destinations; international orders fly out and clear customs.

Promises follow DataCo's scheduled days (Same Day = 20 h). Only orders a carrier could
normally meet are picked, so risk comes from disruptions, as in real operations.
International promises add a 4-day transit allowance to the scheduled days.
"""
from __future__ import annotations

import random
from datetime import timedelta

import numpy as np
import pandas as pd

from . import config, db
from .data import haversine_km, load_orders
from .eta import _fmt, elapsed_with_rest, now
from .geo import geocode
from .model import predict_late_proba
from .routing import min_distance_km, point_along, route_options, save_route_cache

DOMESTIC = {"Estados Unidos", "Canada"}
FEATURE_COLS = ["mode", "market", "order_region", "order_country", "customer_segment", "customer_state",
                "department", "category", "payment_type", "scheduled_days", "store_lat", "store_lon",
                "sales", "quantity", "lines"]
INTL_ALLOWANCE_DAYS = 4


def promise_hours(mode: str, scheduled_days: int) -> float:
    return 20.0 if scheduled_days == 0 else scheduled_days * 24.0


def _candidates(df: pd.DataFrame, domestic: bool, days: int) -> pd.DataFrame:
    df = df[df["delivery_status"] != "Shipping canceled"]
    recent = df[df["order_date"] > df["order_date"].max() - pd.Timedelta(days=days)]
    if domestic:
        mainland = (recent.store_lon < -66) & (recent.store_lat > 24) & (recent.store_lat < 50)
        return recent[mainland & recent.order_country.isin(DOMESTIC)].sort_values("order_date", ascending=False)
    return recent[~recent.order_country.isin(DOMESTIC)].sort_values("order_date", ascending=False)


def _features(row) -> dict:
    f = {c: (row[c].item() if hasattr(row[c], "item") else row[c]) for c in FEATURE_COLS}
    return {k: (str(v) if isinstance(v, str) or not isinstance(v, (int, float, np.integer, np.floating)) else float(v))
            for k, v in f.items()}


def build_fleet(n_truck: int = config.FLEET_SIZE_TRUCK, n_intl: int = config.FLEET_SIZE_INTL,
                seed: int = config.RANDOM_SEED, allow_network: bool = True) -> list[dict]:
    rng = random.Random(seed)
    df = load_orders()
    start = now()
    rows: list[dict] = []

    # Trucks: US destinations first (about two thirds of the fleet), then Canada; one per city.
    seen_cities = set()
    quotas = [("Estados Unidos", round(n_truck * 2 / 3)), ("Canada", n_truck)]
    pool = _candidates(df, True, 2000)
    for country, quota in quotas:
        for _, o in pool[pool.order_country == country].iterrows():
            if sum(r["kind"] == "truck" for r in rows) >= min(quota if country != "Canada" else n_truck, n_truck):
                break
            city_key = (str(o.order_city), str(o.order_state))
            if city_key in seen_cities:
                continue
            dest = geocode(o.order_city, o.order_country, o.order_state, allow_network=allow_network)
            if not dest:
                continue
            straight = float(haversine_km(o.store_lat, o.store_lon, dest["lat"], dest["lon"]))
            if straight < 150:
                continue
            route = route_options((float(o.store_lat), float(o.store_lon)), (dest["lat"], dest["lon"]))[0]
            wall_h, _ = elapsed_with_rest(route["duration_h"])
            promise_h = promise_hours(str(o["mode"]), int(o.scheduled_days))
            if wall_h > promise_h * 0.9:  # carrier would not accept this promise for this lane
                continue
            seen_cities.add(city_key)
            progress = round(rng.uniform(0.1, 0.45), 2)
            pos = point_along(route["geometry"], progress)
            dispatched = start - timedelta(hours=wall_h * progress)
            rows.append({
                "id": f"TRK-{len([r for r in rows if r['kind'] == 'truck']) + 1:03d}", "order_id": int(o.order_id),
                "kind": "truck", "origin_name": f"DataCo store ({o.customer_state})",
                "origin_lat": float(o.store_lat), "origin_lon": float(o.store_lon),
                "dest_city": dest.get("name_en") or str(o.order_city), "dest_state": str(o.order_state), "dest_country": str(o.order_country),
                "dest_lat": dest["lat"], "dest_lon": dest["lon"], "market": str(o.market), "mode": str(o["mode"]),
                "scheduled_days": int(o.scheduled_days), "order_value": round(float(o.sales), 2),
                "dispatched_at": _fmt(dispatched), "promised_at": _fmt(dispatched + timedelta(hours=promise_h)),
                "progress": progress, "current_lat": round(pos[0], 5), "current_lon": round(pos[1], 5),
                "active_option": "R1", "features": _features(o), "late_prob": None, "status": "IN_TRANSIT"})

    # International
    seen_dest = set()
    for _, o in _candidates(df, False, 120).iterrows():
        if sum(r["kind"] == "international" for r in rows) >= n_intl:
            break
        if (o.order_city, o.order_country) in seen_dest:
            continue
        dest = geocode(o.order_city, o.order_country, None, allow_network=allow_network)
        if not dest:
            continue
        seen_dest.add((o.order_city, o.order_country))
        progress = round(rng.uniform(0.0, 0.3), 2)
        promise_h = (int(o.scheduled_days) + INTL_ALLOWANCE_DAYS) * 24.0
        dispatched = start - timedelta(hours=24 * progress * 3)
        rows.append({
            "id": f"INT-{len([r for r in rows if r['kind'] == 'international']) + 1:03d}", "order_id": int(o.order_id),
            "kind": "international", "origin_name": f"DataCo store ({o.customer_state})",
            "origin_lat": float(o.store_lat), "origin_lon": float(o.store_lon),
            "dest_city": dest.get("name_en") or str(o.order_city), "dest_state": str(o.order_state), "dest_country": str(o.order_country),
            "dest_lat": dest["lat"], "dest_lon": dest["lon"], "market": str(o.market), "mode": str(o["mode"]),
            "scheduled_days": int(o.scheduled_days), "order_value": round(float(o.sales), 2),
            "dispatched_at": _fmt(dispatched), "promised_at": _fmt(dispatched + timedelta(hours=promise_h)),
            "progress": progress, "current_lat": float(o.store_lat), "current_lon": float(o.store_lon),
            "active_option": "standard", "features": _features(o), "late_prob": None, "status": "IN_TRANSIT"})

    # ML late-risk for every shipment, as of its dispatch time
    feats = pd.DataFrame([{**r["features"], "order_date": r["dispatched_at"]} for r in rows])
    probs = predict_late_proba(feats)
    for r, p in zip(rows, probs):
        r["late_prob"] = round(float(p), 3)
    return rows


def seed_disruptions(rows: list[dict]) -> list[int]:
    """Today's disruption feed: incidents placed on real routes so the scenario is visible.

    Weather is not seeded: it comes live from Open-Meteo.
    """
    db.clear_incidents()
    ids = []
    trucks = [r for r in rows if r["kind"] == "truck"]
    closure_truck = None
    # 1. Road closure on a long route that has an alternative avoiding it
    for s in sorted(trucks, key=lambda r: -float(haversine_km(r["current_lat"], r["current_lon"], r["dest_lat"], r["dest_lon"]))):
        opts = route_options((s["current_lat"], s["current_lon"]), (s["dest_lat"], s["dest_lon"]))
        if len(opts) < 2:
            continue
        spot = point_along(opts[0]["geometry"], 0.35)
        if min_distance_km(opts[1]["geometry"], spot) > 25:
            ids.append(db.add_incident("road_closure", f"Highway closed after a multi-vehicle crash (on {s['id']}'s route to {s['dest_city']})",
                                       spot[0], spot[1], radius_km=12, delay_h=0, source="scenario"))
            closure_truck = s["id"]
            break
    # 2. Heavy congestion and 3. storm cell on two other trucks' routes
    others = [t for t in trucks if t["id"] != closure_truck]
    if len(others) >= 2:
        a, b = others[0], others[len(others) // 2]
        ra = route_options((a["current_lat"], a["current_lon"]), (a["dest_lat"], a["dest_lon"]))[0]
        spot = point_along(ra["geometry"], 0.5)
        ids.append(db.add_incident("congestion", f"Heavy congestion: lane closures for roadworks (on {a['id']}'s route)",
                                   spot[0], spot[1], radius_km=20, delay_h=5.5, source="scenario"))
        rb = route_options((b["current_lat"], b["current_lon"]), (b["dest_lat"], b["dest_lon"]))[0]
        spot = point_along(rb["geometry"], 0.6)
        ids.append(db.add_incident("storm", f"Severe thunderstorm cell with hail (on {b['id']}'s route)",
                                   spot[0], spot[1], radius_km=60, speed_penalty=0.35, source="scenario"))
    # 4. Customs hold in the most common international destination
    intl = [r for r in rows if r["kind"] == "international"]
    if intl:
        country = pd.Series([r["dest_country"] for r in intl]).value_counts().index[0]
        ids.append(db.add_incident("customs_hold", f"Customs system outage: extra inspections in {country}",
                                   0.0, 0.0, radius_km=0, delay_h=60, country=country, source="scenario"))
    return ids


def reset_scenario(allow_network: bool = True) -> dict:
    """Rebuild the fleet as of now and reseed the disruption feed (clears decisions/notices)."""
    rows = build_fleet(allow_network=allow_network)
    db.replace_shipments(rows)
    with db.conn() as c:
        c.execute("DELETE FROM decisions")
        c.execute("DELETE FROM notifications")
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ('decisions', 'notifications', 'incidents')")
    incident_ids = seed_disruptions(rows)
    for r in rows:  # pre-compute routes from each truck's current position, then persist them
        if r["kind"] == "truck":
            route_options((r["current_lat"], r["current_lon"]), (r["dest_lat"], r["dest_lon"]))
    save_route_cache()
    return {"shipments": len(rows), "trucks": sum(r["kind"] == "truck" for r in rows),
            "international": sum(r["kind"] == "international" for r in rows), "incidents": len(incident_ids)}


def ensure_fleet(allow_network: bool = True) -> None:
    if not db.get_shipments():
        reset_scenario(allow_network=allow_network)
