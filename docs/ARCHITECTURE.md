# Architecture

## Components

| Layer | File | Responsibility |
|---|---|---|
| Data | `src/data.py` | Condense the raw DataCo CSV to an order-level parquet; dataset summary; haversine |
| Model | `src/model.py` | LightGBM late-delivery classifier, 3-window chronological backtest, auto-train on first use |
| Geocoding | `src/geo.py` | Open-Meteo geocoding (Spanish DataCo names → coordinates + English display names), cached |
| Routing | `src/routing.py` | OSRM routes with alternatives (two public servers + offline fallback), committed route cache, segment distance |
| Weather | `src/weather.py` | Open-Meteo hourly forecast at 5 points per route, one batched request per shipment, 30-min cache |
| Customs | `src/customs.py` | Clearance hours by country and the international options |
| ETA engine | `src/eta.py` | Per-option ETA (weather, incidents, rest breaks), SLA status, total cost, recommendation |
| Fleet | `src/fleet.py` | Build today's fleet from recent DataCo orders; seed the disruption scenario |
| Tools | `src/tools.py` | 10 tool schemas (OpenAI function calling) and the dispatcher; parallel fleet evaluation |
| Agent | `src/agent.py` | LLM tool loop (Groq/OpenAI/Ollama) and the offline planner with the triage workflow |
| Persistence | `src/db.py` | SQLite: shipments, incidents, decisions (PENDING → APPROVED/REJECTED/SUPERSEDED), notices |
| API | `src/api.py` | FastAPI endpoints for the UI |
| UI | `frontend/src/app/page.tsx`, `components/TopBar.tsx`, `components/FleetMap.tsx`, `components/RouteComparison.tsx` | Next.js dashboard: header with Back button and tabs, React-Leaflet map, side-by-side route comparison |

## Agent tools

| Tool | Purpose |
|---|---|
| `scan_fleet` | Evaluate every shipment; list breaches, at-risk ones and cheaper options |
| `get_shipment` | Shipment details |
| `predict_delay_risk` | LightGBM late probability for the lane and service level |
| `check_weather` | Live weather along the routes and the speed penalty |
| `check_incidents` | Closures, congestion and storms on the routes |
| `check_customs` | Clearance time, active holds, customs options |
| `get_route_options` | ETA, SLA and cost for every route or customs option, with a breakdown and a comparison against the current plan |
| `propose_reroute` | Create a PENDING decision for the dispatcher (idempotent) |
| `draft_customer_notice` | Draft a delay notice with the new ETA (not sent) |
| `report_disruption` | Log an incident the dispatcher reports ("road closed near Denver") |

## Request flow

1. The UI calls `POST /agent {query}`.
2. `run_agent` uses the LLM tool loop when a key is configured, otherwise `_deterministic`.
3. The tools call the ETA engine, which combines OSRM routes, Open-Meteo weather, the incident feed
   and the customs model. Each request is cached.
4. The response contains the answer and every step as `{thought, tool, args, observation}`.
5. Proposals appear in the Approvals tab. `POST /decisions/{id}/approve` switches the shipment's
   active route and supersedes its other pending proposals.

## Key decisions

- **The model scores risk; live data computes ETAs.** DataCo's delay *days* are not predictable
  beyond the shipping-mode average, so the ETA comes from routes, weather and incidents, and the
  model contributes the historical risk of the lane.
- **Optimise total cost, not just ETA.** Fuel, driver overtime and SLA penalties are weighed
  together, so the agent will sometimes keep a late plan when fixing it costs more than the penalty.
- **Human in the loop.** The agent only proposes reroutes and drafts notices. A dispatcher decides.
- **Free, keyless services with fallbacks.** If OSRM or Open-Meteo is down, the app degrades to
  straight-line estimates or "weather unavailable" instead of failing. Committed caches make the
  demo reproducible offline.
- **Light on memory.** The order-level parquet takes about 4 MB in memory, training takes about
  11 s, and the whole backend runs comfortably on an 8 GB laptop.
