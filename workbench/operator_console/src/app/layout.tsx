import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Metorite Operator Console",
  description: "Staff-only, cross-org customer management (CP-8).",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
