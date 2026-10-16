"use client";

import React from "react";

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span aria-hidden="true" style={{
      display: "inline-block", width: size, height: size, border: "2px solid var(--border-strong)",
      borderTopColor: "var(--teal)", borderRadius: "50%", animation: "spin 0.8s linear infinite",
    }} />
  );
}

export function LoadingStatus({ visible, message }: { visible: boolean; message: string }) {
  if (!visible) return null;
  return (
    <div role="status" aria-live="polite" style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", color: "var(--ink-2)", fontSize: 13 }}>
      <Spinner />
      <span>{message}</span>
    </div>
  );
}
