"""Data, model, routing geometry, ETA/SLA/cost rules and customs."""
from datetime import timedelta

import pandas as pd
import pytest

from src import config, customs, eta, routing
from src.data import dataset_summary, load_orders
from src.model import FEATURES, load_metrics


def test_slim_dataset_is_order_level_and_complete():
    df = load_orders()
    assert len(df) == 65752 and df["order_id"].is_unique
    assert df["order_date"].min() == pd.Timestamp("2015-01-01")
    assert df["order_date"].max().year == 2018
    s = dataset_summary()
    assert 0.5 < s["late_rate"] < 0.6
    assert s["late_rate_by_mode"]["First Class"] > 0.9  # DataCo quirk the report discusses


def test_model_uses_no_leaky_columns_and_beats_baseline():
    for leaky in ("delivery_status", "real_days", "order_status", "shipping_date", "late", "delay_days"):
        assert leaky not in FEATURES
    m = load_metrics()
    assert m is not None, "run python scripts/train.py"
    mean = m["backtest"]["mean"]
    assert mean["model"]["AUC"] > mean["mode_only_baseline"]["AUC"] > mean["overall_rate_baseline"]["AUC"]
    assert mean["model"]["brier"] < mean["mode_only_baseline"]["brier"]
    for w in m["backtest"]["windows"]:  # chronological: every test block starts after its training data
        assert w["train"]["to"] <= w["test"]["from"]


def test_min_distance_measures_segments_not_just_vertices():
    line = [[40.0, -100.0], [40.0, -90.0]]  # vertices ~850 km apart
    assert routing.min_distance_km(line, (40.0, -95.0)) < 1.0
    assert 100 < routing.min_distance_km(line, (41.0, -95.0)) < 120


def test_point_along_endpoints():
    line = [[0.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
    assert routing.point_along(line, 0) == (0.0, 0.0)
    assert routing.point_along(line, 1) == (1.0, 1.0)


def test_offline_routing_fallback():
    r = routing.route_options((10.0, 10.0), (11.0, 11.0))
    assert r[0]["source"].startswith("offline") and r[0]["distance_km"] > 150


@pytest.mark.parametrize("drive,wall,rests", [(5, 5, 0), (11, 11, 0), (12, 22, 1), (30, 50, 2)])
def test_hours_of_service_rest(drive, wall, rests):
    assert eta.elapsed_with_rest(drive) == (wall, rests)


def test_driver_overtime_and_sla_penalty():
    assert eta.driver_cost(8) == 8 * config.DRIVER_WAGE_PER_H
    assert eta.driver_cost(10) == 8 * 28 + 2 * 28 * 1.5
    assert eta.sla_penalty(0, 1000) == 0
    assert eta.sla_penalty(1, 100) == config.SLA_PENALTY_MIN            # floor applies
    assert eta.sla_penalty(25, 10000) == 2 * 0.05 * 10000                # 2 started days, 5% each


def _truck(promised_in_h: float) -> dict:
    t = eta.now()
    return {"id": "TRK-T", "kind": "truck", "current_lat": 40.0, "current_lon": -100.0, "dest_lat": 40.0,
            "dest_lon": -90.0, "promised_at": eta._fmt(t + timedelta(hours=promised_in_h)), "order_value": 500.0,
            "active_option": "R1", "progress": 0.2, "dest_country": "Estados Unidos"}


def _two_routes(monkeypatch):
    south = [[40.0, -100.0], [38.0, -95.0], [40.0, -90.0]]
    north = [[40.0, -100.0], [42.0, -95.0], [40.0, -90.0]]
    monkeypatch.setattr(eta, "route_options", lambda o, d: [
        {"route_id": "R1", "distance_km": 900, "duration_h": 10, "geometry": south, "source": "test", "via": "southern"},
        {"route_id": "R2", "distance_km": 980, "duration_h": 11, "geometry": north, "source": "test", "via": "northern"}])


def test_road_closure_blocks_route_and_reroute_is_recommended(monkeypatch):
    _two_routes(monkeypatch)
    closure = {"id": 1, "kind": "road_closure", "title": "closed", "lat": 38.0, "lon": -95.0, "radius_km": 15,
               "delay_h": 0, "speed_penalty": 0, "country": None}
    e = eta.evaluate(_truck(30), incidents=[closure], with_weather=False)
    assert e["current"]["blocked"] and e["current"]["sla"]["status"] == "BREACH"
    assert e["best"]["option"] == "R2" and e["best"]["sla"]["status"] == "ON_TIME"
    assert e["recommend_change"] and e["saving"] > 0


def test_breakdown_adds_up_and_comparison_matches_costs(monkeypatch):
    _two_routes(monkeypatch)
    closure = {"id": 1, "kind": "road_closure", "title": "closed", "lat": 38.0, "lon": -95.0, "radius_km": 15,
               "delay_h": 0, "speed_penalty": 0, "country": None}
    e = eta.evaluate(_truck(30), incidents=[closure], with_weather=False)
    for o in e["options"]:
        b = o["breakdown"]
        parts = b["free_flow_h"] + b["weather_h"] + b["storm_h"] + b["congestion_h"] + b["closure_h"]
        assert parts == pytest.approx(o["drive_h"], abs=0.2)
        assert b["trip_h"] == pytest.approx(o["drive_h"] + b["rest_h"], abs=0.2)
        assert b["regular_h"] + b["overtime_h"] == pytest.approx(o["drive_h"], abs=0.2)
    c = e["comparisons"]["R2"]
    assert c["from"] == "R1" and c["to"] == "R2" and "R1" not in e["comparisons"]
    assert c["saving"]["total"] == pytest.approx(e["current"]["cost"]["total"] - e["best"]["cost"]["total"], abs=0.01)
    assert c["distance_km"] == 80 and c["eta_change_h"] < 0          # longer road, but arrives sooner
    assert c["disruptions_avoided"] == ["road_closure"] and c["sla"]["to"] == "ON_TIME"


def test_no_change_when_current_route_is_cheapest(monkeypatch):
    _two_routes(monkeypatch)
    e = eta.evaluate(_truck(30), incidents=[], with_weather=False)
    assert e["best"]["option"] == "R1" and not e["recommend_change"]


def test_congestion_adds_delay(monkeypatch):
    _two_routes(monkeypatch)
    jam = {"id": 2, "kind": "congestion", "title": "jam", "lat": 38.0, "lon": -95.0, "radius_km": 20,
           "delay_h": 5, "speed_penalty": 0, "country": None}
    base = eta.evaluate(_truck(30), incidents=[], with_weather=False)["options"][0]["drive_h"]
    hit = eta.evaluate(_truck(30), incidents=[jam], with_weather=False)["options"][0]["drive_h"]
    assert hit == pytest.approx(base + 5)


def test_customs_hold_and_expedited_broker():
    t = eta.now()
    s = {"id": "INT-T", "kind": "international", "current_lat": 35.0, "current_lon": -80.0, "dest_lat": -33.9,
         "dest_lon": 151.2, "dest_country": "Australia", "promised_at": eta._fmt(t + timedelta(hours=60)),
         "order_value": 300.0, "active_option": "standard", "progress": 0.0}
    hold = {"id": 3, "kind": "customs_hold", "title": "outage", "lat": 0, "lon": 0, "radius_km": 0,
            "delay_h": 60, "speed_penalty": 0, "country": "Australia"}
    no_hold = eta.evaluate(s, incidents=[], with_weather=False)
    held = eta.evaluate(s, incidents=[hold], with_weather=False)
    std = lambda e: next(o for o in e["options"] if o["option"] == "standard")
    assert std(held)["customs_h"] == std(no_hold)["customs_h"] + 60
    assert held["best"]["option"] in ("expedited_broker", "express_air") and held["recommend_change"]
    assert customs.clearance_hours("Nowhere") == customs.DEFAULT_CLEARANCE_H
