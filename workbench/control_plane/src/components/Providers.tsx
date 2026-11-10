"use client";

import { SessionProvider } from "next-auth/react";
import { ThemeProvider } from "next-themes";
import ViewModeProvider from "@/components/ViewModeProvider";
import AccessProvider from "@/components/AccessProvider";
import AppearanceProvider from "@/components/ThemeProvider";
import { DEFAULT_MODE } from "@/lib/theme/storage";

export default function Providers({
  children,
}: {
  children: React.ReactNode;
  session?: never;
}) {
  return (
    <SessionProvider refetchOnWindowFocus={false}>
      {/* next-themes owns the light/dark axis (the `.light` / `.dark` class).
          AppearanceProvider owns density and accent, and keeps all three
          apart for each signed-in account (`lib/theme/scope.ts`). */}
      <ThemeProvider attribute="class" defaultTheme={DEFAULT_MODE} enableSystem={false} disableTransitionOnChange>
        <ViewModeProvider>
          {/* Inside SessionProvider: access is resolved for the signed-in
              member, so it must not mount before the session exists. */}
          <AccessProvider>
            {/* Inside AccessProvider: it binds the appearance to the account
                that access names. */}
            <AppearanceProvider />
            {children}
          </AccessProvider>
        </ViewModeProvider>
      </ThemeProvider>
    </SessionProvider>
  );
}