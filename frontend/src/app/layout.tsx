import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Route Intelligence Agent",
  description: "Disruption-aware rerouting and ETA recalculation on DataCo shipments",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
