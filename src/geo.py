"""Destination geocoding via Open-Meteo (free, no key), cached in data/geocache.json.

DataCo names destination countries and US states in Spanish ("Estados Unidos", "Nueva York"),
so lookups use ``language=es`` and match the returned country/state names. The cache is
committed, so the app and tests run offline.
"""
from __future__ import annotations

import json
import threading
import unicodedata

import requests

from .config import GEO_CACHE, GEOCODE_URL, HTTP_TIMEOUT, OFFLINE, USER_AGENT

_LOCK = threading.Lock()
_cache: dict | None = None


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace(".", "").split())


def _key(city: str, country: str, state: str | None) -> str:
    return f"{_norm(city)}|{_norm(country)}|{_norm(state)}"


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(GEO_CACHE.read_text(encoding="utf-8")) if GEO_CACHE.exists() else {}
    return _cache


def _save_cache() -> None:
    GEO_CACHE.parent.mkdir(parents=True, exist_ok=True)
    GEO_CACHE.write_text(json.dumps(_load_cache(), indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _lookup(city: str, country: str, state: str | None) -> dict | None:
    r = requests.get(GEOCODE_URL, params={"name": city, "count": 20, "language": "es", "format": "json"},
                     headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    results = r.json().get("results") or []
    country_n = _norm(country)
    in_country = [x for x in results if _norm(x.get("country")) == country_n]
    if state:
        in_state = [x for x in in_country if _norm(x.get("admin1")) == _norm(state)]
        in_country = in_state or in_country
    if not in_country:
        return None
    best = max(in_country, key=lambda x: x.get("population") or 0)
    name_en = best["name"]
    try:  # English display name for the same place (DataCo uses Spanish names for some cities)
        en = requests.get(GEOCODE_URL.replace("/search", "/get"), params={"id": best["id"], "language": "en"},
                          headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT).json()
        name_en = en.get("name", name_en)
    except (requests.RequestException, ValueError):
        pass
    return {"lat": round(best["latitude"], 5), "lon": round(best["longitude"], 5), "name": best["name"],
            "name_en": name_en,
            "admin1": best.get("admin1"), "country": best.get("country"),
            "country_code": best.get("country_code"), "population": best.get("population")}


def geocode(city: str, country: str, state: str | None = None, allow_network: bool = True) -> dict | None:
    """Coordinates for a DataCo destination, or None if it cannot be resolved."""
    key = _key(city, country, state)
    with _LOCK:
        cache = _load_cache()
        if key in cache:
            return cache[key]
    if OFFLINE or not allow_network:
        return None
    try:
        hit = _lookup(city, country, state)
    except requests.RequestException:
        return None
    with _LOCK:
        _load_cache()[key] = hit  # negative results are cached too, so we never re-query them
        _save_cache()
    return hit
