import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

// The app's two faces, reached through `--font-app` / `--font-app-mono`.
// next/font self-hosts and subsets them, so no request ever leaves for
// Google. Inter, Roboto and Roboto Mono were loaded here until 2026-08-31 —
// they existed only to dress the Fluent and Material themes, and went with
// them, taking three font downloads off every first paint.
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

import AppShell from "@/components/AppShell";
import Providers from "@/components/Providers";
import { ToastProvider } from "@/components/ui/Toast";
import { themeBootScript } from "@/lib/theme/boot";

export const metadata: Metadata = {
  title: "Metorite Control Plane",
  description: "Skill Studio, Chat, Agents and Integrations for the Fracktal AI Company Brain.",
};

// Default mobile-friendly viewport. ViewModeProvider widens this to a desktop
// width at runtime when the user explicitly requests the desktop layout.
// iOS keyboard zoom is prevented via text-[16px] on mobile textareas instead
// of viewport restrictions, so pinch-zoom remains available.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

const fontVariables = [geistSans.variable, geistMono.variable].join(" ");

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${fontVariables} h-full antialiased`}
    >
      <body className="h-full bg-background text-foreground antialiased">
        {/* Applies the stored density and accent before anything paints, so
            neither flashes its default for a frame. There is no theme to
            apply — `globals.css` carries the one look. Mirrors how
            next-themes handles the light/dark class. */}
        <script dangerouslySetInnerHTML={{ __html: themeBootScript() }} />
        {/* WS-27ak(3) — THE confirmation channel, mounted once and above
            everything. Outside `Providers` on purpose: a toast needs no
            session, no theme context and no access decision, and a mutation
            that fails while any of those are re-resolving is exactly when it
            must still be able to say so. It must also outlive the surface that
            raised it — a panel that closes on save is the ordinary case — so it
            cannot live inside a page. Fenced by `conformance.test.ts` rule 8:
            without the provider every `useToast()` call site degrades to a
            silent no-op and no other test in the tree would go red. */}
        <ToastProvider>
          <Providers>
            <AppShell>{children}</AppShell>
          </Providers>
        </ToastProvider>
      </body>
    </html>
  );
}
