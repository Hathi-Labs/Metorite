"use client";

/**
 * The member's shell layout, on the client (NS-7, `navigation_shell.md` §8.2).
 *
 * One read, `GET /api/auth/me/shell`, through the one read cache
 * (`dataCache`). The sidebar, All apps, My Day, the command bar and the first
 * sign-in question all read this key, so a pin toggled in one shows in all of
 * them at once.
 *
 * The rules:
 *
 * - **The first frame draws the last known layout** (round 2). The browser
 *   keeps the layout the server last confirmed, per account
 *   (`shellCache.ts`). The read then confirms it or replaces it. With no
 *   such copy, as on a first visit, "My apps" waits for the read.
 * - **The read starts with the access read, not after it.** The route needs
 *   only the session. So `AppShell` calls this hook before access resolves,
 *   and the answer is often in memory by the first frame of the sidebar.
 * - **A read that fails never blanks the sidebar.** The layout falls back to
 *   the role's preset (`shellLayout`), and the first sign-in question does
 *   not open, because "never asked" is a fact only the server knows.
 * - **A write shows at once.** A pin that the server refuses goes back. An
 *   answer to the question that the server refuses STAYS for this page
 *   (`keep`), so a fault never traps the member in the question. The browser
 *   copy keeps only what the server confirmed.
 */
import { useEffect, useMemo } from "react";
import { useSession } from "next-auth/react";

import { useAccess } from "@/components/AccessProvider";
import { shouldPollWorkspace } from "@/lib/access";
import { invalidate, onClear, peek, put } from "@/lib/dataCache";
import { chatScope } from "@/lib/sessions";
import { useCachedResource } from "@/lib/useCachedResource";

import { EMPTY_SHELL, shellLayout, type ShellLayout, type StoredShell } from "./presets";
import { asLayout, readCachedLayout, writeCachedLayout } from "./shellCache";
import { shellNavOn } from "./shellNav";

export { asLayout } from "./shellCache";

export const SHELL_PREFS_PATH = "/api/auth/me/shell";

/** Ask the first sign-in question again ("Change my layout", rule 3). */
export const CHANGE_LAYOUT = "shell:change-layout";

// ── Page memory ─────────────────────────────────────────────────────────────
// Module state, so it lives as long as the page. `bindIdentity` empties the
// read cache when the member changes, and these go with it.

/** The last layout the SERVER confirmed in this page. Only it is persisted. */
let confirmed: StoredShell | undefined;
/** A layout the member chose that the server refused. It holds for this page. */
let unsaved: StoredShell | undefined;
/** The question was asked in this page. It is never asked twice in one. */
let askedHere = false;

onClear(() => {
  confirmed = undefined;
  unsaved = undefined;
  askedHere = false;
});

/** Tests only: start a new page. */
export function __newShellPageForTests(): void {
  confirmed = undefined;
  unsaved = undefined;
  askedHere = false;
}

export function markAsked(): void {
  askedHere = true;
}

export function askedInThisPage(): boolean {
  return askedHere;
}

/** The layout to keep in the browser: the server's last word, never a guess. */
export function confirmedLayout(): StoredShell | undefined {
  return confirmed;
}

export async function readShellPrefs(): Promise<StoredShell> {
  const res = await fetch(SHELL_PREFS_PATH, { cache: "no-store" });
  if (!res.ok) throw new Error(`shell layout ${res.status}`);
  const layout = asLayout(await res.json());
  if (!layout) throw new Error("shell layout: not a layout");
  confirmed = layout;
  return layout;
}

/**
 * Store `next`, or reset with `null`. It shows at once.
 *
 * On a refusal it throws, so the caller can say so. With `keep`, the chosen
 * layout stays for this page (an answer to the question). Without it, the
 * cache goes back to what it held (a pin).
 */
export async function saveShellPrefs(
  next: StoredShell | null,
  opts: { keep?: boolean } = {},
): Promise<StoredShell> {
  const before = peek<StoredShell>(SHELL_PREFS_PATH)?.data;
  const shown = next ?? EMPTY_SHELL;
  put(SHELL_PREFS_PATH, shown);
  try {
    const res = await fetch(SHELL_PREFS_PATH, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(next),
    });
    if (!res.ok) throw new Error(`shell layout ${res.status}`);
    const saved = asLayout(await res.json()) ?? shown;
    confirmed = saved;
    unsaved = undefined;
    put(SHELL_PREFS_PATH, saved);
    return saved;
  } catch (err) {
    if (opts.keep) {
      unsaved = shown;
      put(SHELL_PREFS_PATH, shown);
    } else if (before) {
      put(SHELL_PREFS_PATH, before);
    } else {
      // Nothing was known before: drop the guess, and read the truth again.
      invalidate(SHELL_PREFS_PATH);
    }
    throw err;
  }
}

export interface ShellPrefsState {
  /** What the shell draws from: the page's choice, the server, or the copy. */
  stored: StoredShell | undefined;
  /**
   * What the SERVER said in this page, or the member's choice in it. Never
   * the browser copy. The first sign-in question opens on this alone.
   */
  fresh: StoredShell | undefined;
  /** What to draw. The role's preset when nothing is known. */
  layout: ShellLayout;
  /** True while nothing is known: no read answered, and no copy. */
  loading: boolean;
  /** True when the last read failed and nothing else is known. */
  failed: boolean;
  /** False with the shell nav off, or with no workspace. */
  enabled: boolean;
}

/**
 * Whether to read the layout. With the shell nav on: once a session exists
 * while access resolves, so the read runs beside the access read, and then
 * only for a member with a workspace (`shouldPollWorkspace`).
 */
export function shellReadEnabled(a: {
  navOn: boolean;
  sessionStatus: string;
  accessLoading: boolean;
  hasWorkspace: boolean;
}): boolean {
  if (!a.navOn) return false;
  return a.accessLoading ? a.sessionStatus === "authenticated" : a.hasWorkspace;
}

/**
 * The layout for this member. With the shell nav off it reads nothing and
 * reports `enabled: false`, so every surface draws as before NS-7.
 */
export function useShellPrefs(): ShellPrefsState {
  const { access, loading: accessLoading } = useAccess();
  const { status } = useSession();
  const hasWorkspace = shouldPollWorkspace(access, accessLoading);
  const reading = shellReadEnabled({
    navOn: shellNavOn(),
    sessionStatus: status,
    accessLoading,
    hasWorkspace,
  });
  const res = useCachedResource<StoredShell>(reading ? SHELL_PREFS_PATH : null, readShellPrefs, {
    // A layout changes when the member changes it, and every write puts the
    // new value into the cache. So a short revisit never asks again.
    ttl: 60_000,
    revalidateOnFocus: false,
  });

  // The account, in the scope the chat caches use. Null until access resolves.
  const enabled = reading && hasWorkspace;
  const scope = enabled ? chatScope(access.email, access.organization?.id) : null;
  const cached = useMemo(() => readCachedLayout(scope), [scope]);
  // Keep what the server confirmed, under this account, after each answer.
  useEffect(() => {
    const layout = confirmedLayout();
    if (scope && layout) writeCachedLayout(scope, layout);
  }, [scope, res.data]);

  const fresh = enabled ? (unsaved ?? res.data) : undefined;
  const stored = enabled ? (fresh ?? cached) : undefined;
  return {
    stored,
    fresh,
    layout: shellLayout(stored, access.roles ?? []),
    loading: enabled && stored === undefined && res.loading,
    failed: enabled && stored === undefined && !res.loading && res.error !== null,
    enabled,
  };
}
