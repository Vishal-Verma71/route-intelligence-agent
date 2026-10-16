"""SQLite persistence: active shipments, disruption incidents, reroute decisions, notifications."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id TEXT PRIMARY KEY,
    order_id INTEGER, kind TEXT,                 -- kind: truck | international
    origin_name TEXT, origin_lat REAL, origin_lon REAL,
    dest_city TEXT, dest_state TEXT, dest_country TEXT, dest_lat REAL, dest_lon REAL,
    market TEXT, mode TEXT, scheduled_days INTEGER, order_value REAL,
    dispatched_at TEXT, promised_at TEXT,
    progress REAL,                               -- share of the planned route already driven
    current_lat REAL, current_lon REAL,
    active_option TEXT,                          -- chosen route id (truck) or customs option (intl)
    features TEXT,                               -- JSON: dispatch-time features for the ML model
    late_prob REAL,
    status TEXT DEFAULT 'IN_TRANSIT'
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, title TEXT, lat REAL, lon REAL, radius_km REAL,
    delay_h REAL, speed_penalty REAL, country TEXT,
    active INTEGER DEFAULT 1, source TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT, from_option TEXT, to_option TEXT,
    eta_before TEXT, eta_after TEXT, cost_before REAL, cost_after REAL,
    sla_before TEXT, sla_after TEXT, reason TEXT,
    status TEXT DEFAULT 'PENDING', decision_key TEXT UNIQUE,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT, channel TEXT, message TEXT, status TEXT DEFAULT 'DRAFT',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _path(path: Path | None) -> Path:
    return Path(path) if path is not None else config.DB_PATH


def init_db(path: Path | None = None) -> None:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as c:
        c.executescript(SCHEMA)


@contextmanager
def conn(path: Path | None = None):
    p = _path(path)
    init_db(p)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _row(r) -> dict:
    d = dict(r)
    if "features" in d and isinstance(d["features"], str):
        d["features"] = json.loads(d["features"])
    return d


# Shipments
def replace_shipments(rows: list[dict]) -> None:
    with conn() as c:
        c.execute("DELETE FROM shipments")
        cols = list(rows[0].keys()) if rows else []
        for r in rows:
            r = {**r, "features": json.dumps(r["features"])}
            c.execute(f"INSERT INTO shipments({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [r[k] for k in cols])


def get_shipments() -> list[dict]:
    with conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM shipments ORDER BY id")]


def get_shipment(shipment_id: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
        return _row(r) if r else None


def set_active_option(shipment_id: str, option: str) -> None:
    with conn() as c:
        c.execute("UPDATE shipments SET active_option=? WHERE id=?", (option, shipment_id))


# Incidents
def add_incident(kind, title, lat, lon, radius_km=30.0, delay_h=0.0, speed_penalty=0.0,
                 country=None, source="user") -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO incidents(kind,title,lat,lon,radius_km,delay_h,speed_penalty,country,source) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (kind, title, lat, lon, radius_km, delay_h, speed_penalty, country, source))
        return cur.lastrowid


def get_incidents(active_only: bool = True) -> list[dict]:
    with conn() as c:
        q = "SELECT * FROM incidents" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
        return [dict(r) for r in c.execute(q)]


def resolve_incident(incident_id: int) -> bool:
    with conn() as c:
        return c.execute("UPDATE incidents SET active=0 WHERE id=? AND active=1", (incident_id,)).rowcount > 0


def clear_incidents() -> None:
    with conn() as c:
        c.execute("DELETE FROM incidents")
        c.execute("DELETE FROM sqlite_sequence WHERE name = 'incidents'")


# Decisions (reroute proposals needing dispatcher approval)
def add_decision(d: dict) -> tuple[int, bool]:
    """Insert a PENDING decision; idempotent on decision_key. Returns (id, created)."""
    with conn() as c:
        row = c.execute("SELECT id FROM decisions WHERE decision_key=?", (d["decision_key"],)).fetchone()
        if row:
            return row["id"], False
        cols = list(d.keys())
        cur = c.execute(f"INSERT INTO decisions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                        [d[k] for k in cols])
        return cur.lastrowid, True


def get_decisions(status: str | None = None) -> list[dict]:
    with conn() as c:
        if status:
            rows = c.execute("SELECT * FROM decisions WHERE status=? ORDER BY id DESC", (status,))
        else:
            rows = c.execute("SELECT * FROM decisions ORDER BY id DESC")
        return [dict(r) for r in rows]


def set_decision_status(decision_id: int, status: str) -> dict:
    if status not in ("APPROVED", "REJECTED"):
        raise ValueError("status must be APPROVED or REJECTED")
    with conn() as c:
        row = c.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(f"Decision {decision_id} not found")
        if row["status"] != "PENDING":
            raise ValueError(f"Decision {decision_id} is already {row['status']}")
        c.execute("UPDATE decisions SET status=? WHERE id=?", (status, decision_id))
        if status == "APPROVED":
            c.execute("UPDATE shipments SET active_option=? WHERE id=?", (row["to_option"], row["shipment_id"]))
            # Any other pending proposal for this shipment is now stale.
            c.execute("UPDATE decisions SET status='SUPERSEDED' WHERE shipment_id=? AND status='PENDING' AND id<>?",
                      (row["shipment_id"], decision_id))
        return {**dict(row), "status": status}


# Notifications (customer delay notices drafted by the agent)
def add_notification(shipment_id: str, channel: str, message: str) -> int:
    with conn() as c:
        existing = c.execute("SELECT id FROM notifications WHERE shipment_id=? AND message=?",
                             (shipment_id, message)).fetchone()
        if existing:
            return existing["id"]
        return c.execute("INSERT INTO notifications(shipment_id,channel,message) VALUES(?,?,?)",
                         (shipment_id, channel, message)).lastrowid


def get_notifications() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM notifications ORDER BY id DESC")]
