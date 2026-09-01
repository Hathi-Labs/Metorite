/**
 * Appearance store.
 *
 * Holds the two-level preference model:
 *
 *   organisation default  — set by an admin, applies to everyone
 *   member override       — optional, wins for that person only
 *
 * A `null` member value means "inherit", which is why overrides are stored as
 * nullable rather than pre-resolved: clearing an override has to fall back to
 * whatever the org default currently is, including later changes to it.
 *
 * ⚠️ **There is no theme here any more.** Owner directive 2026-08-31 retired
 * the theming engine: one look, carried by `globals.css`, for every app. What
 * a member may still change is DENSITY (how big) and ACCENT (which colour) —
 * both of which adjust the one theme rather than replacing it. Colour mode
 * (dark/light) is `next-themes`' own state and deliberately stays there;
 * duplicating it here would give the app two sources for one fact.
 */

import { create } from "zustand";
import type { Density, ThemeMode } from "./types";
import {
  ACCENT_INK_PROPERTIES,
  ACCENT_PROPERTIES,
  DENSITY_PROPERTY,
  densityScale,
  themeStorage,
} from "./storage";
import { isSafeColor } from "./css";
import { accentInk } from "./contrast";

type AppearanceState = {
  /** Member's density override; `null` inherits the org default. */
  userDensity: Density | null;
  /** Member's accent override; `null` keeps the theme's own primary. */
  accent: string | null;

  /** Organisation defaults, refreshed from the backend on mount. */
  orgDensity: Density;
  /** When false, members must use the org default. */
  allowUserOverride: boolean;

  /** True once local preferences have been read out of storage. */
  hydrated: boolean;

  setUserDensity: (density: Density | null) => void;
  setAccent: (color: string | null) => void;
  setOrgDefaults: (org: { density: Density; allowUserOverride: boolean }) => void;
  hydrate: () => void;
};

/** The density actually in force, honouring the org's override policy. */
export function effectiveDensity(s: AppearanceState): Density {
  if (!s.allowUserOverride) return s.orgDensity;
  return s.userDensity ?? s.orgDensity;
}

/**
 * Push the resolved state onto the document. Every mutation funnels through
 * here so the DOM can never disagree with the store.
 *
 * Values reach CSS through `setProperty`, never through generated stylesheet
 * text, so a malformed stored value is rejected by the CSSOM instead of
 * altering the stylesheet.
 */
function applyToDocument(state: AppearanceState): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;

  root.style.setProperty(DENSITY_PROPERTY, String(densityScale(effectiveDensity(state))));

  const accent = state.accent && isSafeColor(state.accent) ? state.accent : null;
  for (const prop of ACCENT_PROPERTIES) {
    if (accent) root.style.setProperty(prop, accent);
    else root.style.removeProperty(prop);
  }
  // An accent brings its own ink. Without this the theme's primary-foreground
  // stays painted on a colour it was never paired with — see accentInk().
  const ink = accent ? accentInk(accent) : "";
  for (const prop of ACCENT_INK_PROPERTIES) {
    if (ink) root.style.setProperty(prop, ink);
    else root.style.removeProperty(prop);
  }
}

export const useAppearanceStore = create<AppearanceState>((set, get) => ({
  userDensity: null,
  accent: null,
  orgDensity: "default",
  allowUserOverride: true,
  hydrated: false,

  setUserDensity: (density) => {
    themeStorage.setDensity(density);
    set({ userDensity: density });
    applyToDocument(get());
  },

  setAccent: (color) => {
    const next = color && isSafeColor(color) ? color : null;
    themeStorage.setAccent(next);
    set({ accent: next });
    applyToDocument(get());
  },

  setOrgDefaults: ({ density, allowUserOverride }) => {
    themeStorage.setOrgDensity(density);
    set({ orgDensity: density, allowUserOverride });
    applyToDocument(get());
  },

  hydrate: () => {
    if (get().hydrated) return;
    const storedOrgDensity = themeStorage.getOrgDensity();
    set({
      userDensity: themeStorage.getDensity(),
      accent: themeStorage.getAccent(),
      orgDensity: storedOrgDensity ?? "default",
      hydrated: true,
    });
    applyToDocument(get());
  },
}));

export type { AppearanceState, ThemeMode };
