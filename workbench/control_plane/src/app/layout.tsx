import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Inter, Roboto, Roboto_Mono } from "next/font/google";
import "./globals.css";

// Every theme's font stack is loaded up front and selected at runtime through
// `--font-app` / `--font-app-mono`, because a theme switch has to be instant —
// fetching a face on switch would show a frame of fallback text. next/font
// self-hosts and subsets each family, so the whole set is a modest one-time
// cost and no request ever leaves for Google.
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// Fluent: stands in for Segoe UI, which the stack prefers when the OS has it.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

// Material 3's type face, and its monospace companion.
const roboto = Roboto({
  variable: "--font-roboto",
  subsets: ["latin"],
  weight: ["300", "400", "500", "700"],
});

const robotoMono = Roboto_Mono({
  variable: "--font-roboto-mono",
  subsets: ["latin"],
});

import AppShell from "@/components/AppShell";
import Providers from "@/components/Providers";
import ThemeStyles from "@/components/ThemeStyles";
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

const fontVariables = [
  geistSans.variable,
  geistMono.variable,
  inter.variable,
  roboto.variable,
  robotoMono.variable,
].join(" ");

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      // Server-rendered default. The boot script below replaces it with the
      // member's stored theme before the first paint; it is set here so the
      // document is never without a theme scope, even with JS disabled.
      data-theme="rapidtool"
      className={`${fontVariables} h-full antialiased`}
    >
      <head>
        <ThemeStyles />
      </head>
      <body className="h-full bg-background text-foreground antialiased">
        {/* Applies the stored theme, density and accent before anything paints.
            Runs ahead of hydration, so there is no flash of the default theme.
            Mirrors how next-themes handles the light/dark class. */}
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
