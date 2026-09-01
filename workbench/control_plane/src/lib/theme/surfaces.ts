"use client";

/**
 * Theme resolution for third-party surfaces.
 *
 * Monaco and Shiki each ship a closed set of named themes and cannot be driven
 * by our CSS custom properties, so they are the one place our look has to name
 * an external vocabulary rather than supply token values.
 *
 * With one theme (owner directive 2026-08-31) these reduce to a lookup by
 * MODE. They stay functions rather than becoming two string constants at the
 * call sites, because "which Monaco theme does a code view use" is a decision
 * that belongs in one place — and because every call site already reads them,
 * so collapsing them would be churn with a chance of divergence.
 */

import { useTheme } from "next-themes";
import { THEME } from "./themes";
import type { ThemeMode } from "./types";

/** Monaco's built-in theme ids — the only values it accepts unregistered. */
export const MONACO_BUILT_INS = ["vs", "vs-dark", "hc-black", "hc-light"] as const;

/** The active colour mode, defaulting to dark as the app does. */
export function useMode(): ThemeMode {
  const { resolvedTheme } = useTheme();
  return resolvedTheme === "light" ? "light" : "dark";
}

/** Monaco editor theme id for the active colour mode. */
export function useMonacoTheme(): string {
  return THEME.surfaces.monaco[useMode()];
}

/** Shiki highlighting theme for the active colour mode. */
export function useShikiTheme(): string {
  return THEME.surfaces.shiki[useMode()];
}
