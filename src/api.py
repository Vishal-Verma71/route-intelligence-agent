"""FastAPI backend for the Route Intelligence UI."""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, eta, fleet, llm, report, tools
from .agent import run_agent
from .data import dataset_summary
from .model import MODEL_NAME, load_metrics, model_ready, train_model


def _warm():
    try:
        tools.evaluate_fleet()  # fills route and weather caches so the first page load is quick
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    fleet.ensure_fleet()
    threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="Route Intelligence Agent API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def _ship(shipment_id: str) -> dict:
    s = db.get_shipment(tools.norm_id(shipment_id) or shipment_id)
    if not s:
        raise HTTPException(404, f"Unknown shipment {shipment_id}")
    return s


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "model_ready": model_ready(),
            "llm_backend": llm.backend_name(), "llm_available": llm.is_available()}


@app.get("/summary")
def summary():
    rows = tools.evaluate_fleet()
    pending = db.get_decisions("PENDING")
    return {
        "shipments": len(rows), "trucks": sum(r["kind"] == "truck" for r in rows),
        "international": sum(r["kind"] == "international" for r in rows),
        "sla": {k: sum(r["sla"] == k for r in rows) for k in ("BREACH", "AT_RISK", "ON_TIME")},
        "reroute_opportunities": sum(r["recommend_change"] for r in rows),
        "potential_saving": round(sum(r["saving"] for r in rows), 2),
        "cost_at_risk": round(sum(r["cost_total"] for r in rows if r["sla"] != "ON_TIME"), 2),
        "pending_decisions": len(pending), "active_incidents": len(db.get_incidents()),
    }


@app.get("/shipments")
def shipments():
    rows = tools.evaluate_fleet()
    geo = {s["id"]: s for s in db.get_shipments()}
    for r in rows:
        s = geo[r["id"]]
        r.update({"current_lat": s["current_lat"], "current_lon": s["current_lon"], "dest_lat": s["dest_lat"],
                  "dest_lon": s["dest_lon"], "origin_lat": s["origin_lat"], "origin_lon": s["origin_lon"],
                  "active_option": s["active_option"], "order_value": s["order_value"]})
    return {"shipments": rows}


@app.get("/shipments/{shipment_id}")
def shipment_detail(shipment_id: str):
    s = _ship(shipment_id)
    e = eta.evaluate(s)
    return {"shipment": {k: v for k, v in s.items() if k != "features"}, "evaluation": e}


@app.get("/incidents")
def incidents():
    return {"incidents": db.get_incidents()}


@app.post("/incidents")
def add_incident(payload: dict = Body(...)):
    obs = tools.execute_tool("report_disruption", payload)
    if obs.get("error"):
        raise HTTPException(400, obs["error"])
    return obs


@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int):
    if not db.resolve_incident(incident_id):
        raise HTTPException(404, f"No active incident {incident_id}")
    return {"resolved": incident_id}


@app.get("/decisions")
def decisions():
    return {"decisions": db.get_decisions()}


def _decide(decision_id: int, status: str):
    try:
        return {"decision": db.set_decision_status(decision_id, status)}
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/decisions/{decision_id}/approve")
def approve(decision_id: int):
    return _decide(decision_id, "APPROVED")


@app.post("/decisions/{decision_id}/reject")
def reject(decision_id: int):
    return _decide(decision_id, "REJECTED")


@app.get("/notifications")
def notifications():
    return {"notifications": db.get_notifications()}


@app.post("/agent")
def agent(payload: dict = Body(...)):
    q = str(payload.get("query", "") or "").strip()
    if not q:
        return JSONResponse(status_code=400, content={"answer": "Please enter a question.", "steps": [],
                                                      "backend": "offline-router"})
    return run_agent(q)


@app.post("/scenario/reset")
def scenario_reset():
    info = fleet.reset_scenario()
    threading.Thread(target=_warm, daemon=True).start()
    return info


@app.get("/evaluation")
def evaluation():
    m = load_metrics()
    return {"status": "OK" if m else "NOT_RUN", "metrics": m, "dataset": dataset_summary()}


@app.post("/train")
def train():
    return {"metrics": train_model()}


@app.get("/report")
def report_md():
    return {"markdown": report.build_report()}
