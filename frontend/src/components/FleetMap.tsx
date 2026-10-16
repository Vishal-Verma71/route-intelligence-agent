"use client";

import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, Popup, TileLayer, Tooltip, useMap } from "react-leaflet";
import { useEffect } from "react";
import { SLA_COLORS } from "./colors";
const OPTION_COLORS = ["#0f766e", "#7c3aed", "#be185d"];
const INCIDENT_COLORS: Record<string, string> = { road_closure: "#c2262e", congestion: "#ea580c", storm: "#7c3aed" };

type Ship = {
  id: string; kind: string; dest: string; sla: string; current_lat: number; current_lon: number;
  dest_lat: number; dest_lon: number; eta: string; recommend_change: boolean;
};
type Option = { option: string; label: string; geometry: [number, number][]; sla: { status: string }; blocked: boolean };
type Incident = { id: number; kind: string; title: string; lat: number; lon: number; radius_km: number };

function incidentIcon(kind: string) {
  const color = INCIDENT_COLORS[kind] ?? "#ef4444";
  return L.divIcon({
    className: "",
    iconSize: [26, 24],
    iconAnchor: [13, 20],
    html: `<svg width="26" height="24" viewBox="0 0 26 24"><path d="M13 2 L25 22 H1 Z" fill="${color}" stroke="#ffffff" stroke-width="2" stroke-linejoin="round"/><path d="M13 9 v6 M13 18 v0.5" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>`,
  });
}

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length > 1) map.fitBounds(points, { padding: [30, 30], maxZoom: 7 });
  }, [map, JSON.stringify(points)]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export default function FleetMap({
  ships, incidents, options, activeOption, selected, onSelect, height = 460, focus = "fleet",
}: {
  ships: Ship[]; incidents: Incident[]; options?: Option[]; activeOption?: string; selected?: string;
  onSelect?: (id: string) => void; height?: number; focus?: "fleet" | "selected";
}) {
  const trucks = ships.filter((s) => s.kind === "truck");
  const sel = ships.find((s) => s.id === selected);
  const fitPoints: [number, number][] =
    focus === "selected" && options?.length
      ? options.flatMap((o) => o.geometry)
      : trucks.flatMap((s) => [[s.current_lat, s.current_lon], [s.dest_lat, s.dest_lon]] as [number, number][]);

  return (
    <div style={{ height, borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }}>
      <MapContainer center={[42, -95]} zoom={4} style={{ height: "100%", width: "100%", background: "#e8eef1" }}
        scrollWheelZoom worldCopyJump>
        {/* Standard OpenStreetMap tiles (free, no key), slightly desaturated so routes stand out. */}
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" className="soft-tiles" />
        <FitBounds points={fitPoints} />

        {incidents.filter((i) => i.kind !== "customs_hold").map((i) => (
          <Circle key={`inc-${i.id}`} center={[i.lat, i.lon]} radius={Math.max(i.radius_km, 8) * 1000}
            pathOptions={{ color: INCIDENT_COLORS[i.kind] ?? "#c2262e", fillOpacity: 0.15, weight: 2, dashArray: "4 4" }}>
            <Tooltip>{`⚠ ${i.title}`}</Tooltip>
          </Circle>
        ))}

        {/* Warning icon at a fixed size so even a 10 km closure stays visible at country zoom,
            and is clearly distinct from the round truck markers. */}
        {incidents.filter((i) => i.kind !== "customs_hold").map((i) => (
          <Marker key={`incm-${i.id}`} position={[i.lat, i.lon]} icon={incidentIcon(i.kind)}>
            <Tooltip direction="top">{`${i.kind.replace("_", " ")}: ${i.title}`}</Tooltip>
          </Marker>
        ))}

        {options?.map((o, k) => (
          <Polyline key={`opt-${o.option}`} positions={o.geometry}
            pathOptions={{
              color: o.blocked ? "#c2262e" : OPTION_COLORS[k % OPTION_COLORS.length],
              weight: o.option === activeOption ? 5 : 3, opacity: o.option === activeOption ? 0.95 : 0.6,
              dashArray: o.option === activeOption ? undefined : "8 6",
            }}>
            <Tooltip sticky>{`${o.label} — ${o.sla.status}${o.blocked ? " (BLOCKED)" : ""}${o.option === activeOption ? " · current" : ""}`}</Tooltip>
          </Polyline>
        ))}

        {trucks.map((s) => (
          <CircleMarker key={s.id} center={[s.current_lat, s.current_lon]} radius={s.id === selected ? 10 : 7}
            pathOptions={{ color: s.id === selected ? "#102a33" : "#ffffff", weight: s.id === selected ? 3 : 2, fillColor: SLA_COLORS[s.sla], fillOpacity: 1 }}
            eventHandlers={{ click: () => onSelect?.(s.id) }}>
            <Tooltip>{`${s.id} → ${s.dest} · ${s.sla}${s.recommend_change ? " · reroute available" : ""}`}</Tooltip>
            <Popup>{`${s.id} → ${s.dest}`}<br />{`ETA ${s.eta.replace("T", " ")} UTC · ${s.sla}`}</Popup>
          </CircleMarker>
        ))}
        {sel && sel.kind === "truck" && (
          <CircleMarker center={[sel.dest_lat, sel.dest_lon]} radius={7}
            pathOptions={{ color: "#ffffff", weight: 2, fillColor: "#0f766e", fillOpacity: 1 }}>
            <Tooltip permanent direction="right">{sel.dest}</Tooltip>
          </CircleMarker>
        )}
      </MapContainer>
    </div>
  );
}
