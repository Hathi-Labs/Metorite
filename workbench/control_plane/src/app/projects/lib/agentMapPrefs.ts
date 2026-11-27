/**
 * The filters that the Agents map keeps for each member (WS-MCP AMR-5, decision
 * G5, `agent_coordination.md` §3.12.8): the People choice and Active or All.
 *
 * They live in `localStorage`. A browser can block it, so each call is inside
 * try and catch. An empty or a bad store gives the defaults.
 */
import type { MapMode } from "./agentScale";

export interface MapPrefs {
  /** "", "me" or an address. */
  person: string;
  mode: MapMode;
}

export const DEFAULT_PREFS: MapPrefs = { person: "", mode: "active" };

type Store = Pick<Storage, "getItem" | "setItem">;

export const prefsKey = (email: string): string => `metorite.agents-map:${email.trim().toLowerCase()}`;

function storeOf(store?: Store): Store | null {
  if (store) return store;
  return typeof window === "undefined" ? null : window.localStorage;
}

export function readPrefs(email: string, store?: Store): MapPrefs {
  try {
    const text = storeOf(store)?.getItem(prefsKey(email));
    const body: unknown = text ? JSON.parse(text) : null;
    if (!body || typeof body !== "object") return DEFAULT_PREFS;
    const { person, mode } = body as { person?: unknown; mode?: unknown };
    return {
      person: typeof person === "string" ? person : DEFAULT_PREFS.person,
      mode: mode === "all" ? "all" : "active",
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function writePrefs(email: string, prefs: MapPrefs, store?: Store): void {
  try {
    storeOf(store)?.setItem(prefsKey(email), JSON.stringify(prefs));
  } catch {
    // A full or a blocked store: the choice lasts for this page only.
  }
}
