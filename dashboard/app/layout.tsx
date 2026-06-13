import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Lucy Ops Command Center",
  description: "Production voice-agent operations dashboard"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

