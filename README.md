# Route Intelligence Agent: Disruption-Aware Rerouting

An agentic workflow for the **Latentsig review, problem statement 1 (Route Intelligence)**. The agent
watches every shipment against **live weather**, **traffic incidents** and **customs delays**,
recalculates ETAs on real alternative routes, weighs fuel, driver overtime and SLA penalties, and
proposes the best plan to a dispatcher. A **LightGBM** model trained on the Kaggle **DataCo Smart
Supply Chain** dataset is its risk tool.

📄 **Full report:** [docs/REPORT.pdf](docs/REPORT.pdf) ([Markdown version](docs/REPORT.md)): problem,
architecture, agent workflow, model evaluation, UI screenshots, a 5-minute demo and the AI tools used.
Components are summarised in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

![Control tower](docs/images/dashboard.png)

## How it meets the brief

| The brief asks for | What this project does |
|---|---|
| A small model as an agent tool | **LightGBM** late-delivery risk model on DataCo (65,752 orders) |
| Handle traffic congestion | Incident feed (closures, congestion) applied to real **OSRM** road routes |
| Handle extreme weather | Live **Open-Meteo** forecasts along every route slow the ETA |
| Handle delayed customs clearances | Customs model with holds; expedited-broker and express-air options |
| Adapt routes dynamically | Compares real alternative routes and proposes the cheapest safe one |
| Recalculate ETAs across dozens of vehicles | One "Triage the fleet" request re-evaluates all 36 shipments |
| Reduce costs and SLA breaches | Total cost = fuel + driver hours with overtime + SLA penalty, with a side-by-side comparison of current vs proposed route |
| DataCo dataset | Orders, store coordinates, destinations, shipping modes, late-delivery flags |
| Report on architecture and workflow | [docs/REPORT.pdf](docs/REPORT.pdf) |
| Bonus: UI/UX | "Control tower" Next.js UI: two-row header with a Back button, live fleet map, agent chat with a trace timeline, side-by-side route comparison, independently scrolling shipment list and detail, approvals |

## Results

- **Agent:** on the demo scenario, one "Triage the fleet" request makes 29 tool calls in a few
  seconds. It finds 7 predicted SLA breaches and 5 better options worth about **$5,960**. For
  example, it reroutes TRK-017 around a highway closure: a 129-hour breach becomes an on-time
  arrival and saves $2,848. Each proposal waits for dispatcher approval. Shipments that are on time
  are never offered a more expensive route. Exact numbers change with the live weather.
- **Model:** in a 3-window chronological backtest it reaches accuracy **72.5%** vs 69.6% for a
  shipping-mode-only baseline (AUC **0.779** vs 0.744). The learning rate (0.1), tree settings,
  features and decision threshold were tuned on a separate validation period. DataCo lateness is
  driven mostly by shipping mode, so higher accuracy would need leaky columns (section 6 of the report).

![Route comparison](docs/images/shipment_detail.png)

## Quickstart

**Prerequisites:** Python 3.10–3.12 and Node.js 20+.

```bash
pip install -r requirements.txt
python -m uvicorn src.api:app --port 8100
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev -- -p 3100
```

Open **http://localhost:3100**. On the first start the backend trains the model (about 15 s) and
builds the fleet from the committed caches.

**Optional, an LLM planner:** copy `.env.example` to `.env`, add a free
[Groq](https://console.groq.com) key (`GROQ_API_KEY=...`), and restart the backend. Without a key,
an offline planner runs the same tools.

> **macOS:** LightGBM needs OpenMP. Run `brew install libomp` once before `pip install`.
> **Linux:** if `import lightgbm` fails, install `libgomp1`.
> Ports 8100 and 3100 avoid clashing with the companion demand-forecasting project (8000 and 3000).

### Try this
1. **Control tower:** red trucks are predicted SLA breaches, and ⚠ marks a disruption. Click a
   truck to open it, and use **← Back** to return.
2. **Agent:** click **▶ Run agent triage** and follow the reasoning-trace timeline.
3. **Shipments:** open TRK-020. The comparison panel shows what switching routes changes: money
   saved, hours sooner, driving hours, rest stops, fuel, CO₂ and each cost line, with the formulas.
4. **Approvals:** approve a reroute. The truck switches route and its SLA status updates.
5. **Disruptions:** report "Storm near Chicago", or tell the agent "Road closed near Denver".

## How it works

```
Next.js UI ──REST──▶ FastAPI ──▶ Agent (LLM tool calling or offline planner)
                                   │  10 tools
   scan_fleet · predict_delay_risk · check_weather · check_incidents · check_customs
   get_route_options · propose_reroute · draft_customer_notice · report_disruption · get_shipment
                                   │
     LightGBM risk · ETA/SLA/cost engine · OSRM routes · Open-Meteo weather · customs · SQLite
```

Main API endpoints: `POST /agent`, `GET /summary`, `GET /shipments`, `GET /shipments/{id}`,
`GET|POST /incidents`, `POST /decisions/{id}/approve|reject`, `POST /scenario/reset`,
`GET /evaluation`, `GET /report`. The interactive API docs are at http://localhost:8100/docs.

## Data
- **Source:** Kaggle *DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS*. The 180,519 order lines are
  condensed to 65,752 orders in `data/dataco_slim.parquet` (2.3 MB, committed).
- **Rebuilding from raw data:** put `DataCoSupplyChainDataset.csv` in `data/raw/`, then run
  `python scripts/train.py`.
- **Today's fleet:** the most recent DataCo orders are replayed as in transit now. Routes, weather
  and geocoding are live, and cached in `data/` for offline use.
- **Simulated parts:** the incidents are a seeded scenario plus anything you report. Costs and
  customs times are documented assumptions.

## Tests
```bash
pytest -q
```
35 tests cover the data and model (no leaky features, chronological backtest), segment-distance
geometry, the hours-of-service rest rule, overtime and SLA penalties, a closure blocking a route,
congestion delays, the route breakdown and comparison, customs holds, scenario building, fleet scans, idempotent proposals and approval,
the triage workflow, intent routing, reported disruptions, a mocked LLM tool loop and the API. All
tests run offline, each with its own temporary database.

## Project layout
```
src/            data, model, geo, routing, weather, customs, eta, fleet, tools, agent, db, api, report
frontend/       Next.js dashboard (React-Leaflet map)
scripts/        train.py, tune_model.py, plot_model.py, build_report_pdf.py
data/           order-level DataCo parquet, geocode and route caches
reports/        model metrics
docs/           REPORT.pdf / REPORT.md, ARCHITECTURE.md, images/
tests/          pytest suite
```

## Limitations
- Truck positions, incidents, costs and customs times are simulated or assumed. DataCo has no
  telematics, traffic or customs data.
- OSRM gives free-flow driving times. Live traffic speeds would need a paid API.
- DataCo's late flag is dominated by shipping mode, which limits what any model can learn from it.

## AI tools leveraged
Claude Code (Claude Opus) for design, implementation, testing and documentation. LightGBM, pandas,
FastAPI, Next.js and React-Leaflet for the system. OSRM, Open-Meteo and OpenStreetMap as free
services. Groq, OpenAI or Ollama LLMs serve as the optional planner. Details are in section 10 of
the report.
