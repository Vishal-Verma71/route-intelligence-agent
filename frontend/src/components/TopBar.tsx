"use client";

import { useEffect, useState } from "react";

export type TabKey = "tower" | "agent" | "shipments" | "disruptions" | "approvals" | "report";

const TABS: { key: TabKey; label: string }[] = [
  { key: "tower", label: "Control tower" },
  { key: "agent", label: "Agent" },
  { key: "shipments", label: "Shipments" },
  { key: "disruptions", label: "Disruptions" },
  { key: "approvals", label: "Approvals" },
  { key: "report", label: "Report" },
];

function UtcClock() {
  const [now, setNow] = useState<string>("");
  useEffect(() => {
    const tick = () => setNow(new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC");
    tick();
    const t = setInterval(tick, 30_000);
    return () => clearInterval(t);
  }, []);
  return <span className="clock">{now || "—"}</span>;
}

const LABELS = Object.fromEntries(TABS.map((t) => [t.key, t.label])) as Record<TabKey, string>;

export function TopBar({ tab, onTab, onBack, backTo, pending, modelReady, llm, planner }: {
  tab: TabKey; onTab: (t: TabKey) => void; onBack?: () => void; backTo?: TabKey; pending: number; modelReady: boolean;
  llm: boolean; planner: string;
}) {
  return (
    <header className="topbar">
      <div className="topbar-row brand-row">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="5" cy="19" r="2.2" /><circle cx="19" cy="5" r="2.2" />
            <path d="M7 19h7a3.5 3.5 0 000-7h-4a3.5 3.5 0 010-7h7" />
          </svg>
        </div>
        <div>
          <div className="brand-name">Route Intelligence</div>
          <div className="brand-sub">Logistics control tower</div>
        </div>
      </div>
      </div>
      <div className="topbar-row controls-row">
      <button className="back-btn" onClick={onBack} disabled={!onBack}
        title={onBack && backTo ? `Back to ${LABELS[backTo]}` : "Nothing to go back to"} aria-label="Back">
        <span aria-hidden="true">←</span> Back
      </button>
      <nav className="nav" aria-label="Sections">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => onTab(t.key)}>
            {t.label}{t.key === "approvals" && pending > 0 && <span className="count">{pending}</span>}
          </button>
        ))}
      </nav>
      <div className="status">
        <span className="hide-sm"><span className="dot" style={{ background: modelReady ? "var(--green)" : "var(--amber)" }} />
          Risk model {modelReady ? "ready" : "training"}</span>
        <span className="hide-sm"><span className="dot" style={{ background: llm ? "var(--green)" : "var(--muted)" }} />
          Planner: {planner}</span>
        <UtcClock />
      </div>
      </div>
    </header>
  );
}
