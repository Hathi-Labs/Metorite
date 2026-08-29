import type { ReactNode } from "react";
import "./globals.css";

import { DEFAULT_THEME, bootScript } from "@/lib/theme";

export const metadata = {
  title: "Metorite Operator Console",
  description: "Staff-only, cross-org customer management (CP-8).",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // 🔴 `suppressHydrationWarning` is required, not decorative. The boot script
    // below rewrites `data-theme` before React hydrates, so the server's markup
    // and the client's DOM legitimately differ on this one attribute. Without
    // it React logs a hydration mismatch on every page load.
    <html lang="en" data-theme={DEFAULT_THEME} suppressHydrationWarning>
      <head>
        {/* Applies the stored theme BEFORE first paint. Without this the page
            renders dark and snaps to light, which reads as the app breaking. */}
        <script dangerouslySetInnerHTML={{ __html: bootScript() }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
