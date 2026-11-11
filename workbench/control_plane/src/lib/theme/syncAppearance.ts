"use client";

/**
 * The member's appearance follows the member to a second device (PKG-20,
 * `project-docs/specs/package_host.md` §9.5).
 *
 * Mode, density and accent live in the browser (`storage.ts`), per account.
 * The gateway keeps one more copy for each member in each organization, in
 * `user_settings.appearance`, behind `/api/auth/me/appearance`. This file is
 * the one client of that route.
 *
 * The rules:
 *
 * 1. The browser copy stays the first paint. The boot script paints it, and
 *    this file never runs before the bind of `scope.ts`.
 * 2. After `reconcileAppearanceScope` binds the account, `ThemeProvider`
 *    calls `startAppearanceSync`. It reads the server copy ONCE for the scope
 *    `<email>|<orgId>`. It applies a field only when it differs from the
 *    browser copy, and only when the member changed no appearance in this
 *    page session (`touched`). An org that locks the density keeps its own:
 *    the density is not applied.
 * 3. A failed read, or an all-null copy, changes nothing. A 404 means the
 *    gateway keeps the feature dark for this org (`PACKAGE_HOST_ORGS`). It
 *    reads as "off": nothing applies, and this page session sends no PUT.
 * 4. The apply goes through the store setters with the origin "server", and
 *    through next-themes `setTheme` for the mode. It never touches the DOM.
 * 5. Only a MEMBER change writes the server copy: `setUserDensity` or
 *    `setAccent` with the origin "member" (`onMemberAppearanceChange`), or
 *    the mode through `useMemberMode`. The write sends the whole
 *    `{mode, density, accent}`, 500 ms after the last change, and a failure
 *    is silent. The apply of a server copy never writes, so two browsers
 *    cannot write each other in a loop.
 * 6. A member change sets an "unsent" mark for the scope in the browser. The
 *    mark holds the time of the change, in epoch ms. A good write clears it,
 *    but only when the mark is not newer than the change that the write
 *    carried, so one tab never clears the mark of a newer change in another
 *    tab. A 404 clears it, and no mark is set while the feature is off.
 * 7. The bind always reads the server copy first. With a mark, the page
 *    sends the browser copy only when the mark is newer than the server
 *    `updated_at`. Otherwise it applies the server copy and clears the mark.
 *    So a failed write, or a tab that closes inside the 500 ms, never lets an
 *    older server copy replace the last choice, and an old choice does not
 *    replace a newer copy of another device (last write wins).
 *    The limit: the mark is the browser clock, and `updated_at` is the server
 *    clock. When the two clocks differ, the wrong side can win. With clocks
 *    that NTP keeps in step, the window is a few seconds. A mark exists only
 *    after a failed write or a tab that closed inside the 500 ms.
 * 8. Only one write is out at a time. A change while a write is out sends
 *    the newest record once, when the answer comes. So the server ends on
 *    the newest change.
 * 9. The write goes only to the bound scope. A passive tab (another tab moved
 *    the pointer to another account) writes nothing.
 *
 * Fence: `syncAppearance.test.ts`. The colour rule is `css.ts::isSafeColor`,
 * and `tests/fixtures/accent_samples.json` holds it to the gateway's port.
 */

import { useCallback } from "react";
import { useTheme } from "next-themes";

import { isSafeColor } from "./css";
import { appearanceScopeFor } from "./scope";
import {
  APPEARANCE_UNSENT_PREFIX,
  DEFAULT_MODE,
  isAppearancePassive,
  tabAppearanceScope,
  themeStorage,
} from "./storage";
import { onMemberAppearanceChange, useAppearanceStore } from "./store";
import { DENSITY_SCALE, type Density, type ThemeMode } from "./types";

export const APPEARANCE_URL = "/api/auth/me/appearance";

/** The wait after the last member change before the write. */
export const WRITE_DELAY_MS = 500;

/** What the member chooses. The server adds `updated_at`. */
export type AppearanceRecord = {
  mode: ThemeMode;
  density: Density | null;
  accent: string | null;
};

/** The server copy, as the route answers it. */
export type ServerAppearance = {
  mode: ThemeMode | null;
  density: Density | null;
  accent: string | null;
  updated_at: string | null;
};

/** How the apply reaches next-themes. `ThemeProvider` gives its `setTheme`. */
export type AppearanceApply = { setMode: (mode: ThemeMode) => void };

const sync = {
  /** The member changed appearance in this page session. */
  touched: false,
  /** The scope this page bound, or null before the bind. */
  bound: null as string | null,
  /** The scope whose one read has started. */
  readFor: null as string | null,
  /** A member change came before the bind. The bind sends it. */
  pending: false,
  timer: null as ReturnType<typeof setTimeout> | null,
  /** The last mode the member chose in this page, or null. */
  memberMode: null as ThemeMode | null,
  /** The gateway answered 404: the feature is dark for this org. */
  off: false,
  /** The time (epoch ms) of the last member change in this page, or 0. */
  lastChange: 0,
  /** A write is out. No second write starts before it answers. */
  inFlight: false,
  /** A change came while the write was out. Its answer sends the newest record. */
  again: false,
  /** Goes up at each new page session of the tests. An old write then changes no state. */
  session: 0,
};

function modeOf(value: unknown): ThemeMode {
  return value === "light" || value === "dark" ? value : DEFAULT_MODE;
}

function densityOf(value: unknown): Density | null {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(DENSITY_SCALE, value) ? (value as Density) : null;
}

function accentOf(value: unknown): string | null {
  return typeof value === "string" && isSafeColor(value) ? value : null;
}

/** The record a write sends: what this page shows for the member now. */
export function currentAppearance(): AppearanceRecord {
  const s = useAppearanceStore.getState();
  return {
    mode: modeOf(themeStorage.getMode() ?? sync.memberMode),
    // The member's own choice, also when the org locks the density.
    density: densityOf(s.userDensity),
    accent: accentOf(s.accent),
  };
}

/** True when this tab may read and write for `scope` now. */
function owns(scope: string): boolean {
  return !isAppearancePassive() && tabAppearanceScope() === scope;
}

function unsentKey(scope: string): string {
  return `${APPEARANCE_UNSENT_PREFIX}${scope}`;
}

/** The time of the unsent mark of `scope`, or null when it has none. */
export function unsentMark(scope: string): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(unsentKey(scope));
    const at = raw === null ? NaN : Number(raw);
    return Number.isFinite(at) ? at : null;
  } catch {
    return null;
  }
}

function markUnsent(scope: string | null, at: number): void {
  if (!scope || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(unsentKey(scope), String(at));
  } catch {
    // No storage: no mark, and the server copy applies at the next bind.
  }
}

/**
 * Clear the mark of `scope`. With `upTo`, clear it only when the mark is not
 * newer than `upTo`. A newer mark belongs to a change that is not sent yet.
 */
function clearUnsent(scope: string, upTo?: number): void {
  if (typeof window === "undefined") return;
  if (upTo !== undefined) {
    const at = unsentMark(scope);
    if (at !== null && at > upTo) return;
  }
  try {
    window.localStorage.removeItem(unsentKey(scope));
  } catch {
    // No storage: nothing to clear.
  }
}

/**
 * Send what this page shows to the server copy of `scope`. Only one write is
 * out at a time. A call while a write is out asks for one more write. That
 * write reads the record when it starts, so it carries the newest change.
 */
async function send(scope: string): Promise<void> {
  if (sync.off) return;
  if (sync.inFlight) {
    sync.again = true;
    return;
  }
  sync.inFlight = true;
  const session = sync.session;
  // The time of the newest change that this record carries. A page with no
  // change of its own sends the mark of an earlier page.
  const carried = sync.lastChange || unsentMark(scope) || 0;
  const record = currentAppearance();
  try {
    const res = await fetch(APPEARANCE_URL, {
      method: "PUT",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
    });
    if (res.status === 404) {
      // The feature is dark for this org. Nothing is to sync, so no mark stays.
      if (session === sync.session && sync.bound === scope) sync.off = true;
      clearUnsent(scope);
    } else if (res.ok) {
      clearUnsent(scope, carried);
    }
  } catch {
    // Silent: the mark stays, and the next bind sends the browser copy.
  }
  if (session !== sync.session) return;
  sync.inFlight = false;
  if (!sync.again) return;
  sync.again = false;
  const next = sync.bound;
  if (next && !sync.off && owns(next)) await send(next);
}

function flush(): void {
  sync.timer = null;
  const scope = sync.bound;
  if (!scope) {
    sync.pending = true;
    return;
  }
  if (owns(scope)) void send(scope);
}

/**
 * A member changed the mode, the density or the accent in this page. It
 * stops the apply of a server copy, and sends the whole record 500 ms after
 * the last change. It sets the unsent mark, but not while the feature is off.
 */
export function noteMemberAppearanceChange(): void {
  sync.touched = true;
  // Each change gets a later time than the one before, also inside one ms.
  sync.lastChange = Math.max(Date.now(), sync.lastChange + 1);
  if (!sync.off) markUnsent(tabAppearanceScope(), sync.lastChange);
  if (sync.timer) clearTimeout(sync.timer);
  sync.timer = setTimeout(flush, WRITE_DELAY_MS);
}

/** A member chose `mode` (`useMemberMode`). */
export function noteMemberMode(mode: ThemeMode): void {
  sync.memberMode = modeOf(mode);
  noteMemberAppearanceChange();
}

// The store tells each density and accent change of the member. A change
// with the origin "server" (the apply below) is not told.
onMemberAppearanceChange(noteMemberAppearanceChange);

/**
 * The colour mode, for a control that the member clicks. The same as
 * next-themes `useTheme`, and a change also writes the server copy. A mode
 * that the page applies by itself uses `setTheme` direct, and writes nothing.
 */
export function useMemberMode(): { mode: string | undefined; setMode: (mode: ThemeMode) => void } {
  const { theme, setTheme } = useTheme();
  const setMode = useCallback(
    (mode: ThemeMode) => {
      setTheme(mode);
      noteMemberMode(mode);
    },
    [setTheme],
  );
  return { mode: theme, setMode };
}

/**
 * The server copy, or null when there is none to apply: a malformed answer,
 * or a copy with no `updated_at` (the all-null answer of a member who never
 * saved one).
 */
export function parseServerAppearance(body: unknown): ServerAppearance | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  if (typeof b.updated_at !== "string" || !b.updated_at) return null;
  return {
    mode: b.mode === "light" || b.mode === "dark" ? b.mode : null,
    density: densityOf(b.density),
    accent: accentOf(b.accent),
    updated_at: b.updated_at,
  };
}

/**
 * Apply each field of `server` that differs from the browser copy. Returns
 * the names of the fields it applied. It writes nothing to the server.
 */
export function applyServerAppearance(server: ServerAppearance, apply: AppearanceApply): string[] {
  const applied: string[] = [];
  if (server.mode && server.mode !== modeOf(themeStorage.getMode())) {
    apply.setMode(server.mode);
    applied.push("mode");
  }
  const store = useAppearanceStore.getState();
  // An org that locks the density keeps it. The member's choice still goes
  // to the server on a member change.
  if (store.allowUserOverride && server.density !== densityOf(store.userDensity)) {
    store.setUserDensity(server.density, "server");
    applied.push("density");
  }
  if (server.accent !== accentOf(store.accent)) {
    store.setAccent(server.accent, "server");
    applied.push("accent");
  }
  return applied;
}

async function readAndApply(scope: string, apply: AppearanceApply): Promise<void> {
  let body: unknown;
  try {
    const res = await fetch(APPEARANCE_URL, { cache: "no-store" });
    if (res.status === 404) {
      // The feature is dark for this org. No mark stays, and no PUT goes.
      if (sync.bound === scope) sync.off = true;
      clearUnsent(scope);
      return;
    }
    if (!res.ok) return;
    body = await res.json();
  } catch {
    // A failed read changes nothing. A mark stays for the next bind.
    return;
  }
  // A member change while the read was out wins, and its own write follows.
  // Another account wins too.
  if (sync.touched || sync.bound !== scope || !owns(scope)) return;
  const server = parseServerAppearance(body);
  const mark = unsentMark(scope);
  if (mark !== null) {
    const serverAt = server?.updated_at ? Date.parse(server.updated_at) : NaN;
    if (!Number.isFinite(serverAt) || mark > serverAt) {
      // A choice of this browser never reached the server, and it is newer
      // than the server copy. Send it, and do not apply the server copy.
      void send(scope);
      return;
    }
    // The server copy is as new as the mark, or newer: another device wrote
    // it after the choice of this browser. The server copy wins.
    clearUnsent(scope, mark);
  }
  if (server) applyServerAppearance(server, apply);
}

/**
 * Start the sync for the account `ThemeProvider` just bound. It sends a
 * member change that came before the bind, and reads the server copy once for
 * each scope. The read decides between an unsent mark and the server copy by
 * their times. It does nothing with no full scope, in a passive tab, or when
 * this tab shows another scope.
 */
export function startAppearanceSync(
  email: string | null | undefined,
  organizationId: string | null | undefined,
  apply: AppearanceApply,
): Promise<void> {
  const scope = appearanceScopeFor(email, organizationId);
  if (!scope || !owns(scope)) return Promise.resolve();
  if (sync.bound && sync.bound !== scope) {
    // Another account in the same page. The old account's change stays with
    // the old account: it is not sent to this one.
    if (sync.timer) clearTimeout(sync.timer);
    sync.timer = null;
    sync.touched = false;
    sync.memberMode = null;
    sync.lastChange = 0;
    sync.again = false;
    // The 404 of one org says nothing of another org. The read of the new
    // scope finds out again.
    sync.off = false;
  }
  sync.bound = scope;
  if (sync.pending) {
    sync.pending = false;
    // The change had no scope to mark. It has one now.
    if (!sync.off) markUnsent(scope, sync.lastChange);
    void send(scope);
  }
  if (sync.readFor === scope) return Promise.resolve();
  sync.readFor = scope;
  return readAndApply(scope, apply);
}

/** Tests only: start a new page session. The store listener stays. */
export function __resetAppearanceSyncForTests(): void {
  if (sync.timer) clearTimeout(sync.timer);
  sync.touched = false;
  sync.bound = null;
  sync.readFor = null;
  sync.pending = false;
  sync.timer = null;
  sync.memberMode = null;
  sync.off = false;
  sync.lastChange = 0;
  sync.inFlight = false;
  sync.again = false;
  sync.session += 1;
}
