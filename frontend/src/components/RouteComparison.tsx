"use client";

import React from "react";

// Side-by-side comparison of the current plan and one alternative, with every figure traceable
// to its inputs. Data comes from the backend's eta.evaluate(): options, comparisons, assumptions.

const money = (n: number) => `$${Math.round(Math.abs(n)).toLocaleString()}`;
const num = (n: number, d = 0) => Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: d });
const dur = (h: number) => {
  const a = Math.abs(h);
  if (a < 24) return `${num(a, 1)} h`;
  const d = Math.floor(a / 24), r = Math.round(a - d * 24);
  return `${num(a, 1)} h (${d} d${r ? ` ${r} h` : ""})`;
};
const when = (s: string) => s.replace("T", " ");
const human = (s: string) => s.replace(/_/g, " ").toLowerCase();

type Row = { label: string; cur: React.ReactNode; alt: React.ReactNode; diff?: number | null; fmt?: (n: number) => string; better?: "lower" | "higher"; note?: string };
type Group = { title: string; rows: Row[] };

function Diff({ row }: { row: Row }) {
  const d = row.diff;
  if (d == null || Math.abs(d) < 0.05) return <span style={{ color: "var(--muted)" }}>same</span>;
  const good = row.better ? (row.better === "lower" ? d < 0 : d > 0) : null;
  const color = good == null ? "var(--ink-2)" : good ? "var(--green)" : "var(--red)";
  return <span style={{ color, fontWeight: 600 }}>{d > 0 ? "+" : "−"}{(row.fmt ?? ((n) => num(n, 1)))(d)}</span>;
}

function slackText(o: any) {
  const s = o.sla.slack_h;
  return s >= 0 ? `${dur(s)} early` : `${dur(-s)} late`;
}

export default function RouteComparison({ ev, ship, target, onTarget, onPropose }: {
  ev: any; ship: any; target: string; onTarget: (t: string) => void; onPropose: () => void;
}) {
  const alts: string[] = Object.keys(ev.comparisons || {});
  if (!alts.length) return null;
  // On time, and every other option costs more: there is nothing worth proposing.
  if (ev.current.sla.status === "ON_TIME" && !ev.recommend_change) {
    return (
      <div className="cmp">
        <div className="cmp-title">No other route proposed</div>
        <div className="cmp-sub">This shipment is on time and every other option costs more, so the agent keeps the current plan.</div>
      </div>
    );
  }
  const pick = alts.includes(target) ? target : alts.includes(ev.best.option) ? ev.best.option : alts[0];
  const cur = ev.current, alt = ev.options.find((o: any) => o.option === pick), c = ev.comparisons[pick];
  const a = ev.assumptions || {};
  const truck = ev.kind === "truck";
  const cb = cur.breakdown, ab = alt.breakdown;

  const groups: Group[] = truck ? [
    { title: "Time", rows: [
      { label: "Distance", cur: `${num(cur.distance_km)} km`, alt: `${num(alt.distance_km)} km`, diff: c.distance_km, fmt: (n) => `${num(n)} km`, better: "lower" },
      { label: "Driving time", cur: dur(cur.drive_h), alt: dur(alt.drive_h), diff: c.drive_h, fmt: dur, better: "lower", note: "hours behind the wheel, including delays" },
      { label: "Rest stops", cur: `${cur.rest_stops} × ${a.rest_h} h`, alt: `${alt.rest_stops} × ${a.rest_h} h`, diff: c.rest_stops, fmt: (n) => num(n), better: "lower" },
      { label: "Total trip time", cur: dur(cb.trip_h), alt: dur(ab.trip_h), diff: c.trip_h, fmt: dur, better: "lower", note: "driving + rest" },
      { label: "Arrival (UTC)", cur: when(cur.eta), alt: when(alt.eta), diff: c.eta_change_h, fmt: dur, better: "lower" },
      { label: "Versus promised time", cur: slackText(cur), alt: slackText(alt) },
      { label: "SLA status", cur: <span className={`pill ${cur.sla.status}`}>{human(cur.sla.status)}</span>, alt: <span className={`pill ${alt.sla.status}`}>{human(alt.sla.status)}</span> },
    ] },
    { title: "Where the driving hours come from", rows: [
      { label: "Free-flow driving (OSRM)", cur: `${cb.free_flow_h} h`, alt: `${ab.free_flow_h} h`, diff: ab.free_flow_h - cb.free_flow_h, fmt: dur, better: "lower" },
      { label: "Weather slowdown", cur: `${cb.weather_h} h`, alt: `${ab.weather_h} h`, diff: ab.weather_h - cb.weather_h, fmt: dur, better: "lower" },
      { label: "Storm slowdown", cur: `${cb.storm_h} h`, alt: `${ab.storm_h} h`, diff: ab.storm_h - cb.storm_h, fmt: dur, better: "lower" },
      { label: "Congestion", cur: `${cb.congestion_h} h`, alt: `${ab.congestion_h} h`, diff: ab.congestion_h - cb.congestion_h, fmt: dur, better: "lower" },
      { label: "Road closure (stranded)", cur: `${cb.closure_h} h`, alt: `${ab.closure_h} h`, diff: ab.closure_h - cb.closure_h, fmt: dur, better: "lower" },
    ] },
    { title: "Driver, fuel and emissions", rows: [
      { label: "Regular driver hours", cur: `${cb.regular_h} h`, alt: `${ab.regular_h} h`, diff: ab.regular_h - cb.regular_h, fmt: dur, better: "lower" },
      { label: "Overtime hours (×1.5 pay)", cur: `${cb.overtime_h} h`, alt: `${ab.overtime_h} h`, diff: c.overtime_h, fmt: dur, better: "lower" },
      { label: "Average speed", cur: `${cb.avg_speed_kmh} km/h`, alt: `${ab.avg_speed_kmh} km/h`, diff: ab.avg_speed_kmh - cb.avg_speed_kmh, fmt: (n) => `${num(n, 1)} km/h`, better: "higher" },
      { label: "Diesel used", cur: `${num(cb.fuel_l)} L`, alt: `${num(ab.fuel_l)} L`, diff: c.fuel_l, fmt: (n) => `${num(n)} L`, better: "lower" },
      { label: "CO₂ emitted", cur: `${num(cb.co2_kg)} kg`, alt: `${num(ab.co2_kg)} kg`, diff: c.co2_kg, fmt: (n) => `${num(n)} kg`, better: "lower" },
    ] },
    { title: "Cost", rows: [
      { label: "Fuel", cur: money(cur.cost.fuel), alt: money(alt.cost.fuel), diff: -c.saving.fuel, fmt: money, better: "lower" },
      { label: "Driver wages", cur: money(cur.cost.driver), alt: money(alt.cost.driver), diff: -c.saving.driver, fmt: money, better: "lower" },
      { label: `SLA penalty`, cur: money(cur.cost.sla_penalty), alt: money(alt.cost.sla_penalty), diff: -c.saving.sla_penalty, fmt: money, better: "lower" },
      { label: "Total cost", cur: <b>{money(cur.cost.total)}</b>, alt: <b>{money(alt.cost.total)}</b>, diff: -c.saving.total, fmt: money, better: "lower" },
    ] },
  ] : [
    { title: "Time", rows: [
      { label: "Air time remaining", cur: dur(cb.air_h), alt: dur(ab.air_h), diff: ab.air_h - cb.air_h, fmt: dur, better: "lower" },
      { label: "Handling", cur: dur(cb.handling_h), alt: dur(ab.handling_h), diff: ab.handling_h - cb.handling_h, fmt: dur, better: "lower" },
      { label: "Customs clearance", cur: dur(cb.customs_h), alt: dur(ab.customs_h), diff: c.customs_h, fmt: dur, better: "lower", note: cur.customs_hold_h ? `includes a ${cur.customs_hold_h} h hold` : undefined },
      { label: "Last-mile delivery", cur: dur(cb.last_mile_h), alt: dur(ab.last_mile_h), diff: 0, fmt: dur },
      { label: "Total transit", cur: dur(cb.trip_h), alt: dur(ab.trip_h), diff: c.trip_h, fmt: dur, better: "lower" },
      { label: "Arrival (UTC)", cur: when(cur.eta), alt: when(alt.eta), diff: c.eta_change_h, fmt: dur, better: "lower" },
      { label: "Versus promised time", cur: slackText(cur), alt: slackText(alt) },
      { label: "SLA status", cur: <span className={`pill ${cur.sla.status}`}>{human(cur.sla.status)}</span>, alt: <span className={`pill ${alt.sla.status}`}>{human(alt.sla.status)}</span> },
    ] },
    { title: "Cost", rows: [
      { label: "Air freight share", cur: money(cur.cost.freight), alt: money(alt.cost.freight), diff: -c.saving.freight, fmt: money, better: "lower" },
      { label: "Broker / express surcharge", cur: money(cur.cost.surcharge), alt: money(alt.cost.surcharge), diff: -c.saving.surcharge, fmt: money, better: "lower" },
      { label: "SLA penalty", cur: money(cur.cost.sla_penalty), alt: money(alt.cost.sla_penalty), diff: -c.saving.sla_penalty, fmt: money, better: "lower" },
      { label: "Total cost", cur: <b>{money(cur.cost.total)}</b>, alt: <b>{money(alt.cost.total)}</b>, diff: -c.saving.total, fmt: money, better: "lower" },
    ] },
  ];

  const save = c.saving.total, sooner = -c.eta_change_h;
  const chips: { label: string; value: string; good: boolean | null }[] = [
    { label: save >= 0 ? "Money saved" : "Extra cost", value: money(save), good: save > 0 ? true : save < 0 ? false : null },
    { label: sooner >= 0 ? "Arrives sooner by" : "Arrives later by", value: dur(sooner), good: sooner > 0 ? true : sooner < 0 ? false : null },
    { label: "Delivery promise", value: c.sla.from === c.sla.to && c.sla.from === "BREACH"
        ? `${num(c.sla.late_h_from, 1)} h → ${num(c.sla.late_h_to, 1)} h late`
        : `${human(c.sla.from)} → ${human(c.sla.to)}`, good: c.sla.late_h_to < c.sla.late_h_from ? true : c.sla.late_h_to > c.sla.late_h_from ? false : null },
    truck
      ? { label: c.drive_h <= 0 ? "Less driving" : "More driving", value: dur(c.drive_h), good: c.drive_h < 0 ? true : c.drive_h > 0 ? false : null }
      : { label: c.customs_h <= 0 ? "Faster customs" : "Slower customs", value: dur(c.customs_h), good: c.customs_h < 0 ? true : c.customs_h > 0 ? false : null },
  ];

  const isRecommended = pick === ev.best.option && ev.recommend_change;
  const short = (o: any) => (truck ? `Route ${o.option}` : o.label.split(" + ")[0].replace("Standard air freight", "Standard air"));

  return (
    <div className="cmp">
      <div className="cmp-head">
        <div>
          <div className="cmp-title">Compare with the current plan</div>
          <div className="cmp-sub">{short(cur)} (current) vs {short(alt)}{isRecommended ? " (agent's recommendation)" : ""}</div>
        </div>
        {alts.length > 1 && (
          <div className="seg">
            {alts.map((k) => (
              <button key={k} className={k === pick ? "on" : ""} onClick={() => onTarget(k)}>
                {truck ? `vs ${k}` : `vs ${human(k)}`}{k === ev.best.option ? " ★" : ""}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="cmp-chips">
        {chips.map((ch) => (
          <div key={ch.label} className={`cmp-chip ${ch.good === true ? "good" : ch.good === false ? "bad" : ""}`}>
            <div className="l">{ch.label}</div><div className="v">{ch.value}</div>
          </div>
        ))}
      </div>

      {c.disruptions_avoided.length > 0 && (
        <div className="cmp-note">Avoids: {c.disruptions_avoided.map(human).join(", ")} on the current {truck ? "route" : "plan"}.</div>
      )}

      <div className="cmp-scroll">
        <table className="cmp-table">
          <thead><tr><th></th><th>{short(cur)} <span className="tag">current</span></th><th>{short(alt)}{pick === ev.best.option && <span className="tag good">best</span>}</th><th>Difference</th></tr></thead>
          {groups.map((g) => (
            <tbody key={g.title}>
              <tr className="grp"><td colSpan={4}>{g.title}</td></tr>
              {g.rows.map((r) => (
                <tr key={r.label} className={r.label === "Total cost" ? "total" : ""}>
                  <td>{r.label}{r.note && <div className="note">{r.note}</div>}</td>
                  <td className="mono">{r.cur}</td><td className="mono">{r.alt}</td>
                  <td className="mono">{"diff" in r ? <Diff row={r} /> : ""}</td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>

      <details className="cmp-how">
        <summary>How these numbers are calculated</summary>
        {truck ? (
          <ul>
            <li><b>Free-flow driving</b> is OSRM's road time for the route on today's map, with no traffic.</li>
            <li><b>Weather slowdown</b>: live Open-Meteo forecast at 5 points on the route; the worst one cuts speed (rain −6%, strong wind −8%, fog −12%, heavy rain or snow −15%, thunderstorm or high wind −20%, heavy snow −35%). A reported storm cell replaces it when worse.</li>
            <li><b>Congestion</b> adds the incident's extra hours; a <b>road closure</b> on the route strands the truck for {a.closure_stranded_h} h.</li>
            <li><b>Driving time</b> = free-flow + slowdowns + congestion + closure. <b>Rest</b>: {a.rest_h} h after every {a.max_drive_h_per_day} h of driving (hours-of-service rule). <b>Trip time</b> = driving + rest; <b>arrival</b> = now + trip time.</li>
            <li><b>SLA</b>: on time with at least {a.at_risk_slack_h} h to spare, at risk with 0–{a.at_risk_slack_h} h, breach if late.</li>
            <li><b>Fuel</b> = km × {a.fuel_l_per_100km} L/100 km × ${a.fuel_price_per_l}/L. For {short(alt)}: {num(alt.distance_km)} km → {num(ab.fuel_l)} L → {money(alt.cost.fuel)}. <b>CO₂</b> = litres × {a.co2_kg_per_l} kg.</li>
            <li><b>Driver wages</b>: ${a.driver_wage_per_h}/h for the first {a.overtime_after_h} h of each driving day, ×{a.overtime_multiplier} after that. For {short(alt)}: {ab.regular_h} h × ${a.driver_wage_per_h} + {ab.overtime_h} h × ${a.driver_wage_per_h * a.overtime_multiplier} = {money(alt.cost.driver)}.</li>
            <li><b>SLA penalty</b> = each started day late × the larger of {Math.round(a.sla_penalty_pct_per_day * 100)}% of the order value ({money(ship.order_value)}) and ${a.sla_penalty_min}. {short(cur)} is {cb.late_days} day(s) late → {money(cur.cost.sla_penalty)}.</li>
            <li><b>Total cost</b> = fuel + driver wages + SLA penalty. The agent recommends the option with the lowest total.</li>
          </ul>
        ) : (
          <ul>
            <li><b>Air time</b> = remaining great-circle distance ÷ aircraft speed (express flies faster), slowed by live weather at the destination.</li>
            <li><b>Customs clearance</b> = typical hours for the destination country plus any active hold; an expedited broker or express air shortens it.</li>
            <li><b>Last mile</b> = {cb.last_mile_h} h delivery after clearance. <b>Arrival</b> = now + air + handling + customs + last mile.</li>
            <li><b>SLA penalty</b> = each started day late × the larger of {Math.round(a.sla_penalty_pct_per_day * 100)}% of the order value ({money(ship.order_value)}) and ${a.sla_penalty_min}.</li>
            <li><b>Total cost</b> = air-freight share + broker or express surcharge + SLA penalty.</li>
          </ul>
        )}
      </details>

      <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn sm" onClick={onPropose}>Ask agent to propose {short(alt)}</button>
      </div>
    </div>
  );
}
