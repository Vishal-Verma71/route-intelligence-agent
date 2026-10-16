"use client";

import dynamic from "next/dynamic";
import React, { useEffect, useRef, useState } from "react";
import { LoadingStatus, Spinner } from "@/components/Spinner";
import { TabKey, TopBar } from "@/components/TopBar";
import { SLA_COLORS } from "@/components/colors";
import RouteComparison from "@/components/RouteComparison";

// Leaflet touches `window`, so the map only renders in the browser.
const FleetMap = dynamic(() => import("@/components/FleetMap"), {
  ssr: false,
  loading: () => <div style={{ height: 520, display: "grid", placeItems: "center", color: "var(--muted)" }}>Loading map…</div>,
});

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100";
const money = (n: any) => `$${Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const when = (s?: string) => (s ? s.replace("T", " ") : "—");
const human = (s: string) => s.replace(/_/g, " ");

function Pill({ value }: { value: string }) {
  return <span className={`pill ${value}`}>{human(value)}</span>;
}

function Panel({ title, action, children, bodyStyle }: { title: string; action?: React.ReactNode; children: React.ReactNode; bodyStyle?: React.CSSProperties }) {
  return (
    <section className="panel">
      <div className="panel-head"><h2>{title}</h2>{action}</div>
      <div className="panel-body" style={bodyStyle}>{children}</div>
    </section>
  );
}

export default function Home() {
  const [tab, setTabRaw] = useState<TabKey>("tower");
  const [tabHistory, setTabHistory] = useState<TabKey[]>([]);
  // Every tab change is remembered, so "Back" returns to the screen the user came from.
  function setTab(next: TabKey) {
    if (next === tab) return;
    setTabHistory((h) => [...h.slice(-19), tab]);
    setTabRaw(next);
  }
  function goBack() {
    const prev = tabHistory[tabHistory.length - 1];
    if (!prev) return;
    setTabHistory(tabHistory.slice(0, -1));
    setTabRaw(prev);
    window.scrollTo({ top: 0 });
  }
  const [health, setHealth] = useState<any>(null);
  const [summary, setSummary] = useState<any>(null);
  const [ships, setShips] = useState<any[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [notices, setNotices] = useState<any[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [agentQ, setAgentQ] = useState("");
  const [history, setHistory] = useState<any[]>([]);
  const [agentLoading, setAgentLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [evalData, setEvalData] = useState<any>(null);
  const [reportMd, setReportMd] = useState("");
  const [reportLoading, setReportLoading] = useState(false);
  const [form, setForm] = useState({ kind: "storm", city: "", state: "", country: "Estados Unidos", delay_h: "" });
  const [resetting, setResetting] = useState(false);
  const [filter, setFilter] = useState<"all" | "flagged" | "truck" | "international">("flagged");
  const [cmpTarget, setCmpTarget] = useState("");
  const detailRun = useRef(0);
  const cmpRef = useRef<HTMLDivElement>(null);
  useEffect(() => setCmpTarget(""), [selected]);

  async function getJSON(path: string, init?: RequestInit) {
    const res = await fetch(`${API}${path}`, init);
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || `${path} failed (${res.status})`);
    return j;
  }

  async function refresh() {
    setLoading(true);
    try {
      const [h, s, sh, inc, d, n] = await Promise.all([
        getJSON("/health"), getJSON("/summary"), getJSON("/shipments"), getJSON("/incidents"),
        getJSON("/decisions"), getJSON("/notifications"),
      ]);
      setHealth(h); setSummary(s); setShips(sh.shipments); setIncidents(inc.incidents);
      setDecisions(d.decisions); setNotices(n.notifications);
    } catch (e: any) {
      setMessage(`Cannot reach the backend at ${API}: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(id: string) {
    if (!id) return;
    const run = ++detailRun.current;
    setDetailLoading(true);
    try {
      const j = await getJSON(`/shipments/${id}`);
      if (run === detailRun.current) setDetail(j);
    } finally {
      if (run === detailRun.current) setDetailLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);
  useEffect(() => { if (selected) loadDetail(selected); }, [selected]);
  useEffect(() => {
    if (tab === "report") {
      getJSON("/evaluation").then(setEvalData).catch(() => setEvalData(null));
      setReportLoading(true);
      getJSON("/report").then((j) => setReportMd(j.markdown)).catch((e) => setReportMd(`Report failed: ${e.message}`))
        .finally(() => setReportLoading(false));
    }
  }, [tab]);

  function openShipment(id: string) {
    setSelected(id);
    setTab("shipments");
  }

  async function ask(text?: string) {
    const q = (text ?? agentQ).trim();
    if (!q) return;
    setAgentQ("");
    setTab("agent");
    setAgentLoading(true);
    setHistory((h) => [...h, { role: "user", content: q }]);
    try {
      const j = await getJSON("/agent", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) });
      setHistory((h) => [...h, { role: "agent", content: j.answer, steps: j.steps, backend: j.backend }]);
      refresh();
    } catch (e: any) {
      setHistory((h) => [...h, { role: "agent", content: `Error: ${e.message}`, steps: [] }]);
    } finally {
      setAgentLoading(false);
    }
  }

  async function decide(id: number, action: "approve" | "reject") {
    try {
      const j = await getJSON(`/decisions/${id}/${action}`, { method: "POST" });
      setMessage(`Decision #${id} ${j.decision.status.toLowerCase()}: ${j.decision.shipment_id} ${action === "approve" ? `now uses ${j.decision.to_option}` : "keeps its plan"}.`);
      await refresh();
      if (selected === j.decision.shipment_id) loadDetail(selected);
    } catch (e: any) {
      setMessage(e.message);
    }
  }

  async function addIncident() {
    const body: any = { kind: form.kind };
    if (form.kind === "customs_hold") body.country = form.country;
    else Object.assign(body, { city: form.city, state: form.state || undefined, country: form.country });
    if (form.delay_h) body.delay_h = Number(form.delay_h);
    try {
      const j = await getJSON("/incidents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setMessage(`Incident #${j.incident_id} logged${j.near ? ` near ${j.near}` : ""}; the fleet has been re-evaluated.`);
      setForm({ ...form, city: "", state: "" });
      refresh();
    } catch (e: any) {
      setMessage(e.message);
    }
  }

  async function resolveIncident(id: number) {
    try {
      await getJSON(`/incidents/${id}/resolve`, { method: "POST" });
      setMessage(`Incident #${id} resolved.`);
      refresh();
    } catch (e: any) {
      setMessage(e.message);
    }
  }

  async function resetScenario() {
    setResetting(true);
    try {
      const j = await getJSON("/scenario/reset", { method: "POST" });
      setMessage(`Scenario rebuilt: ${j.trucks} trucks, ${j.international} international shipments, ${j.incidents} incidents.`);
      setHistory([]); setSelected(""); setDetail(null);
      await refresh();
    } catch (e: any) {
      setMessage(e.message);
    } finally {
      setResetting(false);
    }
  }

  function renderMarkdown(md: string) {
    const lines = md.split("\n");
    const out: React.ReactNode[] = [];
    let i = 0, key = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (line.startsWith("# ")) { out.push(<h2 key={key++}>{line.slice(2)}</h2>); i++; continue; }
      if (line.startsWith("## ")) { out.push(<h3 key={key++}>{line.slice(3)}</h3>); i++; continue; }
      if (line.trim().startsWith("|")) {
        const rows: string[][] = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          rows.push(lines[i].trim().split("|").slice(1, -1).map((x) => x.trim())); i++;
        }
        const body = rows.filter((r) => !r.every((c) => /^[-:]+$/.test(c)));
        out.push(
          <table key={key++}><thead><tr>{body[0]?.map((h, k) => <th key={k}>{h}</th>)}</tr></thead>
            <tbody>{body.slice(1).map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{c}</td>)}</tr>)}</tbody></table>);
        continue;
      }
      if (line.trim().startsWith("- ")) { out.push(<div key={key++} style={{ margin: "3px 0 3px 12px" }}>• {line.trim().slice(2)}</div>); i++; continue; }
      if (line.trim()) out.push(<p key={key++} style={{ margin: "6px 0" }}>{line.replace(/\*\*/g, "")}</p>);
      i++;
    }
    return <div className="md">{out}</div>;
  }

  const flagged = ships.filter((s) => s.sla !== "ON_TIME" || s.recommend_change).sort((a, b) => a.slack_h - b.slack_h);
  const visible = filter === "flagged" ? flagged : filter === "all" ? ships : ships.filter((s) => s.kind === filter);
  const pending = decisions.filter((d) => d.status === "PENDING");
  const ev = detail?.evaluation;
  const sh = detail?.shipment;
  const lastAgent = [...history].reverse().find((m) => m.role === "agent" && m.steps?.length);

  return (
    <>
      <TopBar tab={tab} onTab={setTab} onBack={tabHistory.length ? goBack : undefined} backTo={tabHistory[tabHistory.length - 1]} pending={pending.length} modelReady={!!health?.model_ready}
        llm={!!health?.llm_available} planner={health?.llm_available ? `LLM (${health.llm_backend})` : "offline"} />

      <main className="main">
        {message && (
          <div className="panel" style={{ padding: "10px 14px", marginBottom: 14, display: "flex", justifyContent: "space-between", borderLeft: "4px solid var(--teal)" }}>
            <span>{message}</span>
            <button className="btn ghost sm" onClick={() => setMessage("")}>Dismiss</button>
          </div>
        )}

        {tab === "tower" && (
          <>
            <div className="page-title">
              <div>
                <h1>Fleet overview</h1>
                <p>{summary ? `${summary.trucks} trucks on US/Canada roads and ${summary.international} international shipments, re-evaluated against live weather, incidents and customs.` : "Loading the fleet…"}</p>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" disabled={agentLoading} onClick={() => ask("Triage the fleet")}>▶ Run agent triage</button>
                <button className="btn ghost" onClick={refresh} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
                <button className="btn ghost" onClick={resetScenario} disabled={resetting}>{resetting ? "Rebuilding…" : "Reset scenario"}</button>
              </div>
            </div>
            <div className="tower">
              <Panel title="Live fleet map" bodyStyle={{ padding: 10 }} action={
                <div className="legend">
                  <span><span className="dot" style={{ background: SLA_COLORS.ON_TIME }} />On time</span>
                  <span><span className="dot" style={{ background: SLA_COLORS.AT_RISK }} />At risk</span>
                  <span><span className="dot" style={{ background: SLA_COLORS.BREACH }} />Predicted breach</span>
                  <span style={{ color: "var(--red)" }}>▲ Disruption</span>
                </div>}>
                <FleetMap ships={ships} incidents={incidents} selected={selected} onSelect={openShipment} height={560} />
              </Panel>
              <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
                <div className="kpis">
                  <div className="kpi"><div className="label">Active shipments</div><div className="value">{summary?.shipments ?? "…"}</div><div className="note">{summary?.trucks ?? 0} trucks · {summary?.international ?? 0} intl</div></div>
                  <div className="kpi alert"><div className="label">Predicted breaches</div><div className="value">{summary?.sla?.BREACH ?? "…"}</div><div className="note">{summary?.sla?.AT_RISK ?? 0} more at risk</div></div>
                  <div className="kpi good"><div className="label">Better options</div><div className="value">{summary?.reroute_opportunities ?? "…"}</div><div className="note">saves {money(summary?.potential_saving)}</div></div>
                  <div className="kpi warn"><div className="label">Awaiting approval</div><div className="value">{summary?.pending_decisions ?? "…"}</div><div className="note">{summary?.active_incidents ?? 0} disruptions active</div></div>
                </div>
                <section className="panel">
                  <div className="panel-head"><h2>Flagged shipments</h2><span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{flagged.length}</span></div>
                  <div style={{ maxHeight: 392, overflow: "auto" }}>
                    {loading && !ships.length && <div className="panel-body"><LoadingStatus visible message="Evaluating the fleet…" /></div>}
                    {!loading && flagged.length === 0 && <div className="panel-body" style={{ color: "var(--muted)" }}>Every shipment is on time.</div>}
                    {flagged.map((s) => (
                      <div key={s.id} className="feed-item" onClick={() => openShipment(s.id)}>
                        <div className="feed-bar" style={{ background: SLA_COLORS[s.sla] }} />
                        <div>
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                            <span className="mono" style={{ fontWeight: 600 }}>{s.id}</span><Pill value={s.sla} />
                          </div>
                          <div style={{ fontSize: 13, marginTop: 2 }}>{s.dest}</div>
                          <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 2 }}>
                            {s.incidents.map(human).join(", ") || s.weather || "service level"} · slack <span className="mono">{s.slack_h} h</span>
                          </div>
                          {s.recommend_change && <div style={{ fontSize: 12, color: "var(--green)", marginTop: 2 }}>↳ switch to {human(s.best_option)}, saves {money(s.saving)}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              </div>
            </div>
          </>
        )}

        {tab === "agent" && (
          <>
            <div className="page-title">
              <div><h1>Agent</h1><p>Ask in plain language, or report a disruption. Every tool call appears in the trace on the right.</p></div>
              <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>planner: {health?.llm_available ? `LLM (${health.llm_backend})` : "offline (add GROQ_API_KEY for LLM planning)"}</span>
            </div>
            <div className="agent-layout">
              <Panel title="Conversation">
                {history.length === 0 && <div style={{ color: "var(--muted)", marginBottom: 12 }}>Try “Triage the fleet”, “TRK-020”, or “Road closed near Denver”.</div>}
                {history.map((m, i) => (
                  <div key={i} className={`msg ${m.role}`}>
                    <div className="who">{m.role === "user" ? "dispatcher" : `agent · ${m.backend ?? ""}`}</div>
                    {m.content}
                  </div>
                ))}
                <LoadingStatus visible={agentLoading} message="Checking routes, weather, incidents and customs…" />
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <input className="input" style={{ flex: 1 }} placeholder="Message the agent…" value={agentQ}
                    onChange={(e) => setAgentQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} disabled={agentLoading} />
                  <button className="btn" onClick={() => ask()} disabled={agentLoading}>Send</button>
                </div>
                <div className="chips" style={{ marginTop: 10 }}>
                  {["Triage the fleet", "How many breaches are there?", "Show incidents", ...flagged.slice(0, 2).map((s) => s.id),
                    "Storm near Chicago", "Customs hold in India"].map((q) => (
                    <button key={q} className="chip" onClick={() => ask(q)} disabled={agentLoading}>{q}</button>
                  ))}
                </div>
              </Panel>
              <Panel title={`Reasoning trace${lastAgent ? ` · ${lastAgent.steps.length} tool calls` : ""}`} bodyStyle={{ maxHeight: 720, overflow: "auto" }}>
                {!lastAgent && <div style={{ color: "var(--muted)" }}>The agent's tool calls for the latest answer will appear here.</div>}
                {lastAgent && (
                  <div className="timeline">
                    {lastAgent.steps.map((s: any, j: number) => (
                      <div key={j} className={`step ${s.observation?.error ? "err" : ""}`}>
                        <div className="thought">{s.thought}</div>
                        <div className="tool">{s.tool}({Object.entries(s.args || {}).map(([k, v]) => `${k}=${v}`).join(", ")})</div>
                        <details><summary style={{ fontSize: 12, color: "var(--muted)", cursor: "pointer" }}>observation</summary>
                          <pre>{JSON.stringify(s.observation, null, 1).slice(0, 3000)}</pre>
                        </details>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>
            </div>
          </>
        )}

        {tab === "shipments" && (
          <>
            <div className="page-title">
              <div><h1>Shipments</h1><p>Select a shipment to compare every route or customs option on ETA, SLA and total cost.</p></div>
              <div className="chips">
                {(["flagged", "all", "truck", "international"] as const).map((f) => (
                  <button key={f} className="chip" style={filter === f ? { borderColor: "var(--teal)", color: "var(--teal)", fontWeight: 600 } : undefined}
                    onClick={() => setFilter(f)}>{f === "flagged" ? `Flagged (${flagged.length})` : f === "all" ? `All (${ships.length})` : f === "truck" ? "Trucks" : "International"}</button>
                ))}
              </div>
            </div>
            <div className="split split-scroll">
              <section className="panel ship-list">
                <div className="scroll-area">
                  <table className="dt">
                    <thead><tr><th>ID</th><th>Destination</th><th>Service</th><th className="num">Risk</th><th>ETA (UTC)</th><th>SLA</th></tr></thead>
                    <tbody>
                      {visible.map((s) => (
                        <tr key={s.id} className={`clickable ${s.id === selected ? "selected" : ""}`} onClick={() => setSelected(s.id)}>
                          <td className="mono" style={{ fontWeight: 600 }}>{s.id}</td>
                          <td>{s.dest}{s.recommend_change && <div style={{ fontSize: 11, color: "var(--green)" }}>better option: {human(s.best_option)}</div>}</td>
                          <td style={{ color: "var(--ink-2)" }}>{s.mode}</td>
                          <td className="num">{Math.round(s.late_prob * 100)}%</td>
                          <td className="mono" style={{ fontSize: 12 }}>{when(s.eta)}</td>
                          <td><Pill value={s.sla} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="panel ship-detail">
                <div className="panel-head">
                  <h2>{selected ? `${selected}${sh ? ` → ${sh.dest_city}` : ""}` : "Shipment detail"}</h2>
                  {detailLoading && <span style={{ fontSize: 12, color: "var(--teal)", display: "flex", gap: 6, alignItems: "center" }}><Spinner size={12} /> recalculating</span>}
                </div>
                <div className="panel-body scroll-area">
                  {!selected && <div style={{ color: "var(--muted)" }}>Pick a shipment from the list, or click a truck on the control-tower map.</div>}
                  {sh && (
                    <dl className="kv" style={{ marginTop: 0, marginBottom: 12 }}>
                      <dt>Type</dt><dd>{sh.kind === "truck" ? "truck" : "international air + customs"} · {sh.mode}</dd>
                      <dt>Promised</dt><dd>{when(sh.promised_at)} UTC</dd>
                      <dt>Order value</dt><dd>{money(sh.order_value)}</dd>
                      <dt>ML late risk</dt><dd>{Math.round((sh.late_prob ?? 0) * 100)}%</dd>
                    </dl>
                  )}
                  {ev && sh?.kind === "truck" && (
                    <FleetMap ships={ships.filter((s) => s.id === selected)} incidents={incidents} options={ev.options}
                      activeOption={sh.active_option} selected={selected} height={300} focus="selected" />
                  )}
                  {ev && sh && (
                    <div ref={cmpRef}>
                      <RouteComparison ev={ev} ship={sh} target={cmpTarget} onTarget={setCmpTarget}
                        onPropose={() => {
                          const alts = Object.keys(ev.comparisons || {});
                          const t = alts.includes(cmpTarget) ? cmpTarget : alts.includes(ev.best.option) ? ev.best.option : alts[0];
                          ask(`reroute ${selected} to ${t}`);
                        }} />
                    </div>
                  )}
                  {ev && (
                    <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
                      {ev.options.map((o: any) => {
                        const isCur = o.option === sh.active_option, isBest = o.option === ev.best.option;
                        return (
                          <div key={o.option} className={`option ${isBest ? "best" : ""} ${isCur ? "current" : ""}`}>
                            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                              <b style={{ fontSize: 13.5 }}>{o.label}</b>
                              <span style={{ display: "flex", gap: 6 }}>
                                {isCur && <span className="pill DRAFT">current</span>}
                                {isBest && <span className="pill ON_TIME">best</span>}
                                <Pill value={o.sla.status} />
                              </span>
                            </div>
                            <dl className="kv">
                              <dt>ETA</dt><dd>{when(o.eta)} UTC ({o.sla.slack_h >= 0 ? "+" : ""}{o.sla.slack_h} h)</dd>
                              {o.drive_h != null ? <><dt>Driving</dt><dd>{o.drive_h} h · {o.rest_stops} rest stop(s) · {o.distance_km.toLocaleString()} km</dd></> :
                                <><dt>Transit</dt><dd>{o.transit_h} h · customs {o.customs_h} h{o.customs_hold_h ? ` (hold +${o.customs_hold_h} h)` : ""}</dd></>}
                              <dt>Weather</dt><dd>{o.weather.available ? (o.weather.worst ?? "clear") : "unavailable"}{o.weather.penalty ? ` (−${Math.round(o.weather.penalty * 100)}% speed)` : ""}</dd>
                              {o.incidents.length > 0 && <><dt>Disruption</dt><dd style={{ color: "var(--red)" }}>{o.incidents.map((i: any) => human(i.kind)).join(", ")}{o.blocked ? " (blocked)" : ""}</dd></>}
                              <dt>Total cost</dt><dd><b>{money(o.cost.total)}</b> (SLA penalty {money(o.cost.sla_penalty)})</dd>
                            </dl>
                            {!isCur && !(ev.current.sla.status === "ON_TIME" && !ev.recommend_change) && (
                              <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                                <button className="btn sm ghost" onClick={() => { setCmpTarget(o.option); cmpRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>Compare with current</button>
                                <button className="btn sm ghost" onClick={() => ask(`reroute ${selected} to ${o.option}`)}>Ask agent to propose this</button>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </section>
            </div>
          </>
        )}

        {tab === "disruptions" && (
          <>
            <div className="page-title">
              <div><h1>Disruptions</h1><p>Weather is live from Open-Meteo. Log what your team hears about closures, congestion, storms or customs; the fleet is re-evaluated at once.</p></div>
            </div>
            <div style={{ display: "grid", gap: 16 }}>
              <Panel title="Report a disruption">
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                    <option value="storm">Storm</option><option value="road_closure">Road closure</option>
                    <option value="congestion">Congestion</option><option value="customs_hold">Customs hold</option>
                  </select>
                  {form.kind !== "customs_hold" && <>
                    <input className="input" placeholder="City, e.g. Denver" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
                    <input className="input" placeholder="State (optional)" value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} />
                  </>}
                  <select className="input" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })}>
                    {/* Values are DataCo's Spanish country names; labels are English. */}
                    {[["Estados Unidos", "United States"], ["Canada", "Canada"], ["Australia", "Australia"], ["India", "India"],
                      ["China", "China"], ["Indonesia", "Indonesia"], ["Filipinas", "Philippines"], ["Singapur", "Singapore"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                  <input className="input" style={{ width: 120 }} placeholder="Extra hours" value={form.delay_h} onChange={(e) => setForm({ ...form, delay_h: e.target.value })} />
                  <button className="btn" onClick={addIncident} disabled={form.kind !== "customs_hold" && !form.city}>Log disruption</button>
                </div>
              </Panel>
              <section className="panel">
                <div className="panel-head"><h2>Active disruptions</h2><span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{incidents.length}</span></div>
                {incidents.length === 0 && <div className="panel-body" style={{ color: "var(--muted)" }}>No active disruptions.</div>}
                {incidents.map((i) => (
                  <div key={i.id} className="incident">
                    <div style={{ borderRadius: 2, background: i.kind === "storm" ? "#7c3aed" : i.kind === "congestion" ? "#ea580c" : i.kind === "customs_hold" ? "var(--amber)" : "var(--red)" }} />
                    <div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>#{i.id}</span>
                        <b style={{ textTransform: "capitalize" }}>{human(i.kind)}</b>
                        <span className="pill DRAFT">{i.source}</span>
                      </div>
                      <div style={{ marginTop: 3 }}>{i.title}</div>
                      <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 3 }} className="mono">
                        {i.country || `${i.lat.toFixed(2)}, ${i.lon.toFixed(2)} · radius ${i.radius_km} km`} · impact: {i.kind === "road_closure" ? "road blocked" : i.speed_penalty ? `−${Math.round(i.speed_penalty * 100)}% speed` : `+${i.delay_h} h`}
                      </div>
                    </div>
                    <div><button className="btn sm ghost" onClick={() => resolveIncident(i.id)}>Resolve</button></div>
                  </div>
                ))}
              </section>
            </div>
          </>
        )}

        {tab === "approvals" && (
          <>
            <div className="page-title">
              <div><h1>Approvals</h1><p>The agent proposes; a dispatcher decides. Approving switches the shipment to the new route or customs option.</p></div>
            </div>
            <div style={{ display: "grid", gap: 16 }}>
              <section className="panel">
                <div className="panel-head"><h2>Reroute decisions</h2><span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{pending.length} pending · {decisions.length} total</span></div>
                {decisions.length === 0 && <div className="panel-body" style={{ color: "var(--muted)" }}>No proposals yet. Run the agent triage from the control tower.</div>}
                {decisions.map((d) => (
                  <div key={d.id} className="decision">
                    <div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        <span className="mono" style={{ color: "var(--muted)", fontSize: 12 }}>#{d.id}</span>
                        <a className="mono" style={{ fontWeight: 600, cursor: "pointer" }} onClick={() => openShipment(d.shipment_id)}>{d.shipment_id}</a>
                        <span style={{ color: "var(--ink-2)" }}>{human(d.from_option)} → {human(d.to_option)}</span>
                        <Pill value={d.status} />
                      </div>
                      <div className="diff">
                        <div className="side"><Pill value={d.sla_before} /><div className="mono" style={{ marginTop: 4 }}>{when(d.eta_before)}</div><div className="mono">{money(d.cost_before)}</div></div>
                        <div style={{ textAlign: "center", color: "var(--muted)" }}>→</div>
                        <div className={`side after ${d.sla_after}`}><Pill value={d.sla_after} /><div className="mono" style={{ marginTop: 4 }}>{when(d.eta_after)}</div><div className="mono">{money(d.cost_after)}</div></div>
                      </div>
                      <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 8 }}>{d.reason}</div>
                    </div>
                    <div style={{ display: "flex", gap: 6, alignItems: "start" }}>
                      {d.status === "PENDING" && <>
                        <button className="btn sm approve" onClick={() => decide(d.id, "approve")}>Approve</button>
                        <button className="btn sm reject" onClick={() => decide(d.id, "reject")}>Reject</button>
                      </>}
                    </div>
                  </div>
                ))}
              </section>
              <section className="panel">
                <div className="panel-head"><h2>Customer delay notices (drafts)</h2><span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{notices.length}</span></div>
                {notices.length === 0 && <div className="panel-body" style={{ color: "var(--muted)" }}>No notices drafted.</div>}
                {notices.map((n) => (
                  <div key={n.id} className="incident" style={{ gridTemplateColumns: "4px 1fr auto" }}>
                    <div style={{ background: "var(--teal)", borderRadius: 2 }} />
                    <div><span className="mono" style={{ fontWeight: 600 }}>{n.shipment_id}</span> <span style={{ color: "var(--ink-2)" }}>· {n.channel}</span><div style={{ marginTop: 4 }}>{n.message}</div></div>
                    <Pill value={n.status} />
                  </div>
                ))}
              </section>
            </div>
          </>
        )}

        {tab === "report" && (
          <>
            <div className="page-title">
              <div><h1>Report</h1><p>Model quality and an operations report generated from the current fleet state.</p></div>
              <button className="btn ghost" disabled={reportLoading || !reportMd} onClick={() => {
                const url = URL.createObjectURL(new Blob([reportMd], { type: "text/markdown" }));
                const a = document.createElement("a"); a.href = url; a.download = "operations_report.md"; a.click(); URL.revokeObjectURL(url);
              }}>Download Markdown</button>
            </div>
            <div style={{ display: "grid", gap: 16 }}>
              <Panel title="Late-delivery model: chronological backtest">
                {!evalData?.metrics ? <LoadingStatus visible message="Loading evaluation…" /> : (
                  <>
                    <p style={{ marginTop: 0, color: "var(--ink-2)" }}>
                      {evalData.metrics.model}, trained on {evalData.metrics.training_orders.toLocaleString()} delivered DataCo orders.
                      Each window trains only on earlier orders. DataCo lateness is driven mostly by shipping mode, so the mode-only baseline is strong.
                    </p>
                    <table className="dt" style={{ maxWidth: 720 }}>
                      <thead><tr><th>Model</th><th className="num">AUC</th><th className="num">Accuracy</th><th className="num">Brier (lower is better)</th></tr></thead>
                      <tbody>
                        {[["LightGBM", "model"], ["Shipping-mode-only baseline", "mode_only_baseline"], ["Overall late rate", "overall_rate_baseline"]].map(([n, k]) => (
                          <tr key={k}>
                            <td style={{ fontWeight: k === "model" ? 600 : 400 }}>{n}</td>
                            {["AUC", "accuracy", "brier"].map((m) => <td key={m} className="num">{evalData.metrics.backtest.mean[k][m].toFixed(3)}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </Panel>
              <Panel title="Operations report">
                {reportLoading ? <LoadingStatus visible message="Generating the operations report…" /> : renderMarkdown(reportMd)}
              </Panel>
            </div>
          </>
        )}
      </main>
    </>
  );
}
