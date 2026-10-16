"""Road routing with alternatives via public OSRM servers (free, no key), with an offline fallback.

Durations from OSRM are free-flow driving times; live conditions (weather, incidents,
congestion) are applied on top by the ETA engine. Responses are cached in memory so the
public servers are queried once per origin/destination pair.
"""
from __future__ import annotations

import math
import threading

import requests

import json

from .config import HTTP_TIMEOUT, OFFLINE, ROUTE_CACHE, ROUTERS, USER_AGENT
from .data import haversine_km

ROAD_FACTOR = 1.25       # straight-line km -> road km, for the offline fallback
FALLBACK_SPEED_KMH = 80
_cache: dict[str, list[dict]] = {}
_lock = threading.Lock()
_loaded = False


def _load_disk_cache() -> None:
    """Routes for the demo fleet are committed, so a fresh clone is fast and works offline."""
    global _loaded
    if not _loaded:
        _loaded = True
        if ROUTE_CACHE.exists():
            _cache.update(json.loads(ROUTE_CACHE.read_text(encoding="utf-8")))


def save_route_cache() -> None:
    with _lock:
        live = {k: v for k, v in _cache.items() if not v[0]["source"].startswith("offline")}
        ROUTE_CACHE.write_text(json.dumps(live, separators=(",", ":")), encoding="utf-8")


def _fallback(o: tuple[float, float], d: tuple[float, float]) -> list[dict]:
    km = float(haversine_km(o[0], o[1], d[0], d[1])) * ROAD_FACTOR
    mid = ((o[0] + d[0]) / 2, (o[1] + d[1]) / 2)
    return [{"route_id": "R1", "distance_km": round(km, 1), "duration_h": round(km / FALLBACK_SPEED_KMH, 2),
             "geometry": [[o[0], o[1]], [mid[0], mid[1]], [d[0], d[1]]],
             "source": "offline estimate (straight line x 1.25, 80 km/h)", "via": "direct"}]


def _summarise(coords: list[list[float]]) -> str:
    """Rough 'via' label: the compass direction the route bulges towards, for the UI."""
    if len(coords) < 3:
        return "direct"
    (lat0, lon0), (lat1, lon1) = coords[0], coords[-1]
    lat_m, lon_m = coords[len(coords) // 2]
    # signed distance of the midpoint from the straight line
    cross = (lon1 - lon0) * (lat_m - lat0) - (lat1 - lat0) * (lon_m - lon0)
    return "northern" if cross > 0.3 else "southern" if cross < -0.3 else "direct"


def route_options(origin: tuple[float, float], dest: tuple[float, float], alternatives: int = 3) -> list[dict]:
    """Up to ``alternatives`` driving routes, fastest first. ``origin``/``dest`` are (lat, lon)."""
    key = f"{origin[0]:.3f},{origin[1]:.3f};{dest[0]:.3f},{dest[1]:.3f}"
    with _lock:
        _load_disk_cache()
        if key in _cache:
            return _cache[key]
    routes = None
    if not OFFLINE:
        path = f"/route/v1/driving/{origin[1]},{origin[0]};{dest[1]},{dest[0]}"
        params = {"alternatives": alternatives, "overview": "simplified", "geometries": "geojson", "steps": "false"}
        for base in ROUTERS:
            try:
                r = requests.get(base + path, params=params, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
                j = r.json()
                if r.status_code == 200 and j.get("code") == "Ok" and j.get("routes"):
                    routes = []
                    for i, rt in enumerate(j["routes"][:alternatives]):
                        coords = [[round(lat, 5), round(lon, 5)] for lon, lat in rt["geometry"]["coordinates"]]
                        routes.append({"route_id": f"R{i + 1}", "distance_km": round(rt["distance"] / 1000, 1),
                                       "duration_h": round(rt["duration"] / 3600, 2), "geometry": coords,
                                       "source": f"OSRM ({base.split('/')[2]})", "via": _summarise(coords)})
                    break
            except (requests.RequestException, ValueError):
                continue
    routes = routes or _fallback(origin, dest)
    with _lock:
        _cache[key] = routes
    return routes


def point_along(geometry: list[list[float]], fraction: float) -> tuple[float, float]:
    """Position after ``fraction`` (0-1) of a polyline, by distance."""
    if not geometry:
        raise ValueError("empty geometry")
    fraction = min(max(fraction, 0.0), 1.0)
    seg = [float(haversine_km(a[0], a[1], b[0], b[1])) for a, b in zip(geometry, geometry[1:])]
    target, run = sum(seg) * fraction, 0.0
    for (a, b), s in zip(zip(geometry, geometry[1:]), seg):
        if run + s >= target and s > 0:
            t = (target - run) / s
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        run += s
    return tuple(geometry[-1])


def min_distance_km(geometry: list[list[float]], point: tuple[float, float]) -> float:
    """Closest approach of a route polyline to a point, measured to each segment, not just vertices.

    Simplified OSRM geometries have vertices 10-20 km apart, so vertex-only distance would
    miss incidents sitting on the road between two vertices. Uses a local equirectangular
    projection around the point (accurate to well under 1% at these distances).
    """
    lat0, lon0 = point
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 110.57

    def xy(p):
        return ((p[1] - lon0) * kx, (p[0] - lat0) * ky)

    best = float("inf")
    pts = [xy(p) for p in geometry]
    if len(pts) == 1:
        return math.hypot(*pts[0])
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best
