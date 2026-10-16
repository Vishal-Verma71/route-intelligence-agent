"""Live weather along a route from Open-Meteo (free, no key).

Samples points along the route, fetches the next 48 hours of hourly forecasts in one
request, and converts the worst conditions into a speed penalty for the ETA engine.
Results are cached for 30 minutes. When the API is unreachable the tool says so and applies
no penalty, rather than inventing weather.
"""
from __future__ import annotations

import threading
import time

import requests

from .config import HTTP_TIMEOUT, OFFLINE, USER_AGENT, WEATHER_URL
from .routing import point_along

CACHE_TTL_S = 1800
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()

# Speed reductions for trucks, following the usual road-weather guidance (illustrative).
RULES = [
    ("thunderstorm", lambda h: h["weather_code"] >= 95, 0.20),
    ("heavy snow", lambda h: h["snowfall"] >= 1.0, 0.35),
    ("snow", lambda h: h["snowfall"] >= 0.2, 0.15),
    ("heavy rain", lambda h: h["precipitation"] >= 7.0, 0.15),
    ("rain", lambda h: h["precipitation"] >= 2.0, 0.06),
    ("high wind", lambda h: h["wind_speed_10m"] >= 60, 0.20),
    ("strong wind", lambda h: h["wind_speed_10m"] >= 40, 0.08),
    ("fog / low visibility", lambda h: h["visibility"] is not None and h["visibility"] < 1000, 0.12),
]


def _sample_points(geometry: list[list[float]], n: int = 5) -> list[tuple[float, float]]:
    return [point_along(geometry, i / (n - 1)) for i in range(n)]


def _assess(locs: list[dict], pts: list[tuple[float, float]], hours: int) -> dict:
    worst, by_point = 0.0, {}
    for i, loc in enumerate(locs):
        h = loc["hourly"]
        for t in range(min(hours, len(h["time"]))):
            row = {k: (h[k][t] if h[k][t] is not None else (None if k == "visibility" else 0)) for k in
                   ("precipitation", "snowfall", "wind_speed_10m", "visibility", "weather_code")}
            for name, test, pen in RULES:
                if test(row):
                    worst = max(worst, pen)
                    if i not in by_point or pen > by_point[i]["penalty"]:
                        by_point[i] = {"point": i, "time": h["time"][t], "condition": name, "penalty": pen,
                                       "lat": pts[i][0], "lon": pts[i][1]}
                    break
    return {"available": True, "penalty": worst,
            "conditions": sorted(by_point.values(), key=lambda f: -f["penalty"]),
            "source": "Open-Meteo hourly forecast, next 48 h, 5 points along the route"}


def route_weather_many(geometries: list[list[list[float]]], hours: int = 48) -> list[dict]:
    """Weather assessment for several routes with a single Open-Meteo request (cached 30 min)."""
    samples = [_sample_points(g) for g in geometries]
    keys = [tuple((round(a, 1), round(b, 1)) for a, b in pts) for pts in samples]
    results: list[dict | None] = [None] * len(geometries)
    with _lock:
        for i, k in enumerate(keys):
            hit = _cache.get(k)
            if hit and time.time() - hit[0] < CACHE_TTL_S:
                results[i] = hit[1]
    todo = [i for i, r in enumerate(results) if r is None]
    if not todo:
        return results
    if OFFLINE:
        off = {"available": False, "penalty": 0.0, "conditions": [], "source": "weather offline (tests)"}
        return [r or off for r in results]
    pts = [p for i in todo for p in samples[i]]
    try:
        r = requests.get(WEATHER_URL, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}, params={
            "latitude": ",".join(f"{p[0]:.3f}" for p in pts),
            "longitude": ",".join(f"{p[1]:.3f}" for p in pts),
            "hourly": "precipitation,snowfall,wind_speed_10m,visibility,weather_code",
            "forecast_days": 2, "timezone": "UTC"})
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        off = {"available": False, "penalty": 0.0, "conditions": [],
               "source": f"Open-Meteo unavailable ({type(e).__name__}); no weather penalty applied"}
        return [r or off for r in results]
    data = data if isinstance(data, list) else [data]
    for n, i in enumerate(todo):
        res = _assess(data[n * 5:(n + 1) * 5], samples[i], hours)
        results[i] = res
        with _lock:
            _cache[keys[i]] = (time.time(), res)
    return results


def route_weather(geometry: list[list[float]], hours: int = 48) -> dict:
    """Worst forecast conditions along one route and the resulting speed penalty (0-0.35)."""
    return route_weather_many([geometry], hours)[0]


def point_weather(lat: float, lon: float) -> dict:
    """Same assessment for a single location (used for airports and destinations)."""
    return route_weather([[lat, lon], [lat, lon]])
