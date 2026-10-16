"""Project configuration: paths, external APIs and the cost model's business assumptions."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_CSV = DATA_DIR / "raw" / "DataCoSupplyChainDataset.csv"
SLIM_DATA = DATA_DIR / "dataco_slim.parquet"      # committed, so a clone works without the raw download
GEO_CACHE = DATA_DIR / "geocache.json"            # committed city coordinates (Open-Meteo geocoding)
ROUTE_CACHE = DATA_DIR / "route_cache.json"       # committed OSRM responses for the demo fleet
MODEL_DIR = BASE_DIR / "models"
REPORT_DIR = BASE_DIR / "reports"

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
load_dotenv(BASE_DIR / ".env", override=False)

DB_PATH = Path(os.getenv("ROUTE_DB", str(DATA_DIR / "route_intel.db")))

# Optional LLM planner (falls back to the offline planner when no key is set).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")  # auto | groq | openai | ollama | none
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# Free, keyless external services.
ROUTERS = [
    "https://routing.openstreetmap.de/routed-car",  # FOSSGIS OSRM
    "https://router.project-osrm.org",              # OSRM demo server (backup)
]
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
HTTP_TIMEOUT = 12
USER_AGENT = "route-intelligence-demo/1.0 (Latentsig review project)"
OFFLINE = os.getenv("ROUTE_OFFLINE", "0") == "1"   # tests force offline fallbacks

# Fleet
FLEET_SIZE_TRUCK = 24        # "dozens of vehicles" from the brief
FLEET_SIZE_INTL = 12
RANDOM_SEED = 42

# Cost model (documented business assumptions, USD).
FUEL_L_PER_100KM = 32.0       # Class 8 truck
FUEL_PRICE_PER_L = 1.05
CO2_KG_PER_L = 2.68           # diesel combustion
DRIVER_WAGE_PER_H = 28.0
OVERTIME_AFTER_H = 8.0        # per driving day
OVERTIME_MULTIPLIER = 1.5
MAX_DRIVE_H_PER_DAY = 11.0    # US hours-of-service style limit
REST_H = 10.0
SLA_PENALTY_PCT_PER_DAY = 0.05  # 5% of order value per day late
SLA_PENALTY_MIN = 150.0         # minimum penalty per late day (typical B2B contract floor)

# Risk thresholds
LATE_PROB_WARNING = 0.6
