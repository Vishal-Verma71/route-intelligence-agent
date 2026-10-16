"""Fleet scenario, tools, agent planners, dispatcher approval and the HTTP API."""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from src import agent, db, tools


def test_scenario_builds_fleet_and_disruptions(scenario):
    assert scenario == {"shipments": 36, "trucks": 24, "international": 12, "incidents": 4}
    ships = db.get_shipments()
    assert all(0 <= s["late_prob"] <= 1 for s in ships)
    assert {i["kind"] for i in db.get_incidents()} == {"road_closure", "congestion", "storm", "customs_hold"}


def test_scan_finds_the_closure_breach_and_its_fix(scenario):
    scan = tools.execute_tool("scan_fleet", {})
    assert scan["sla_counts"]["BREACH"] >= 1 and scan["reroute_opportunities"] >= 1
    closure_hits = [r for r in scan["at_risk"] if "road_closure" in r["incidents"]]
    assert closure_hits and all(r["recommend_change"] for r in closure_hits)


@pytest.mark.parametrize("text,expected", [("trk-20", "TRK-020"), ("look at INT 3", "INT-003"),
                                           ("TRK-001 please", "TRK-001"), ("no id here", None)])
def test_shipment_id_parsing(text, expected):
    assert tools.norm_id(text) == expected


def test_propose_is_idempotent_and_approval_switches_route(scenario):
    target = next(r for r in tools.evaluate_fleet() if r["recommend_change"])
    first = tools.execute_tool("propose_reroute", {"shipment_id": target["id"]})
    second = tools.execute_tool("propose_reroute", {"shipment_id": target["id"]})
    assert first["decision_id"] == second["decision_id"] and second["status"] == "already pending"
    decided = db.set_decision_status(first["decision_id"], "APPROVED")
    assert db.get_shipment(target["id"])["active_option"] == decided["to_option"]
    with pytest.raises(ValueError):
        db.set_decision_status(first["decision_id"], "REJECTED")


def test_triage_workflow_end_to_end(scenario):
    r = agent._deterministic("Triage the fleet")
    used = [s["tool"] for s in r["steps"]]
    assert used[0] == "scan_fleet"
    for tool in ("predict_delay_risk", "get_route_options", "propose_reroute", "draft_customer_notice"):
        assert tool in used
    assert "predicted SLA breaches" in r["answer"] and "awaiting approval" in r["answer"]
    assert db.get_decisions("PENDING") and db.get_notifications()


@pytest.mark.parametrize("query,first_tool", [
    ("Triage the fleet", "scan_fleet"), ("what needs attention?", "scan_fleet"),
    ("TRK-001", "get_shipment"), ("weather for TRK-002", "check_weather"),
    ("customs INT-001", "check_customs"), ("reroute TRK-003", "propose_reroute"),
    ("How many breaches are there?", "scan_fleet"), ("show incidents", "check_incidents"),
    ("hello", None),
])
def test_router_intents(query, first_tool, monkeypatch):
    called = []
    monkeypatch.setattr(tools, "execute_tool", lambda n, a: called.append(n) or {"error": "stub"})
    agent._deterministic(query)
    assert (called[0] if called else None) == first_tool


def test_reported_disruption_is_logged_then_triaged(scenario, monkeypatch):
    monkeypatch.setattr(tools, "geocode", lambda *a, **k: {"lat": 39.74, "lon": -104.99, "name_en": "Denver"})
    r = agent._deterministic("Road closed near Denver")
    assert [s["tool"] for s in r["steps"]][:2] == ["report_disruption", "scan_fleet"]
    assert any(i["title"].endswith("Denver") for i in db.get_incidents())


def test_llm_tool_loop_with_mock_client(scenario):
    sent = []

    class Completions:
        turn = 0

        def create(self, **kw):
            sent.append(list(kw["messages"]))
            self.turn += 1
            if self.turn == 1:
                call = NS(id="c1", function=NS(name="get_route_options", arguments='{"shipment_id": "TRK-001"}'))
                return NS(choices=[NS(message=NS(content=None, tool_calls=[call]))])
            return NS(choices=[NS(message=NS(content="Done.", tool_calls=None))])

    client = NS(chat=NS(completions=Completions()))
    r = agent.run_agent("compare options for TRK-001", client_factory=lambda: ("mock", client, "m"))
    assert r["backend"] == "mock" and r["answer"] == "Done."
    assert r["steps"][0]["tool"] == "get_route_options" and "error" not in r["steps"][0]["observation"]
    assert sent[1][-2]["tool_calls"][0]["function"]["name"] == "get_route_options"
    assert sent[1][-1]["role"] == "tool" and "geometry" not in sent[1][-1]["content"]


def test_api_endpoints(scenario):
    from src.api import app
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "ok"
        s = c.get("/summary").json()
        assert s["shipments"] == 36 and sum(s["sla"].values()) == 36
        assert c.get("/shipments/TRK-001").status_code == 200
        assert c.get("/shipments/NOPE").status_code == 404
        assert c.post("/incidents", json={"kind": "volcano", "city": "X"}).status_code == 400
        assert c.post("/decisions/999/approve").status_code == 404
        assert c.post("/agent", json={"query": ""}).status_code == 400
        assert "Operations Report" in c.get("/report").json()["markdown"]
