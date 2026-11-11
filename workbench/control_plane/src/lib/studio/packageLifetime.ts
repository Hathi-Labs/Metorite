/**
 * The end-date bar of an app page (`package_host.md` §6.2, slices PKG-10d, PKG-10e-1 and
 * PKG-10e-2, task 2971).
 *
 * A private install ends 30 days after it starts, unless the owner keeps it. This module reads the
 * lifetime of the install of the owner (`GET /api/studio/packages/<id>/lifetime`), and sends Keep
 * and Restore (`POST .../keep` and `POST .../restore`). The bar shows nothing when the read fails.
 * It never shows an error for a read.
 *
 * The days left count from the `now` that the SERVER sends, never from the browser clock. A clock
 * that runs fast or slow must not change what the owner reads.
 *
 * Pure of React. Fence: `packageLifetime.test.ts`.
 */
import { invalidate } from "@/lib/dataCache";

export type InstallState = "active" | "paused" | "archived";

/** The install of the owner, as the gateway sends it. */
export interface LifetimeInstall {
  state: InstallState;
  ends_at: string | null;
  archived_at: string | null;
  restore_until: string | null;
  /** The clock of the server when it made the answer. */
  now: string;
}

export type LifetimeRead = { kind: "bar"; install: LifetimeInstall } | { kind: "none" };
export type LifetimeWrite = { kind: "ok" } | { kind: "error"; status: number; message: string };

export type BarState = "ends" | "archived" | "none";

export const KEEP_FAILED_LINE = "Keep did not work. Try again.";
export const RESTORE_FAILED_LINE = "Restore did not work. Try again.";
export const OFFLINE_LINE = "The request did not go through. Check your connection and try again.";

const DAY_MS = 86_400_000;
const STATES: readonly string[] = ["active", "paused", "archived"];

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** The cache key of the lifetime of one package. */
export const lifetimeKey = (pkg: string): string => `studio:package-lifetime:${pkg}`;

const base = (pkg: string): string => `/api/studio/packages/${encodeURIComponent(pkg)}`;

/** The install of the wire, or null when the body is not one. */
export function parseInstall(body: unknown): LifetimeInstall | null {
  if (!isObject(body) || !isObject(body.install)) return null;
  const i = body.install;
  const now = text(i.now);
  if (typeof i.state !== "string" || !STATES.includes(i.state) || !now || Number.isNaN(Date.parse(now))) return null;
  return {
    state: i.state as InstallState,
    ends_at: text(i.ends_at),
    archived_at: text(i.archived_at),
    restore_until: text(i.restore_until),
    now,
  };
}

/**
 * Read the lifetime. An install gives a bar. A 200 with no install, a 404, any other status,
 * a bad body and a network error give no bar. The bar never shows an error.
 */
export async function getLifetime(pkg: string, fetchImpl: typeof fetch = fetch): Promise<LifetimeRead> {
  try {
    const res = await fetchImpl(`${base(pkg)}/lifetime`);
    if (!res.ok) return { kind: "none" };
    const install = parseInstall(await res.json().catch(() => null));
    return install ? { kind: "bar", install } : { kind: "none" };
  } catch {
    return { kind: "none" };
  }
}

/** One outcome for one POST. The two callers below write their own literal path, which the parity fence reads. */
async function outcome(call: () => Promise<Response>, fallback: string): Promise<LifetimeWrite> {
  try {
    const res = await call();
    if (res.ok) return { kind: "ok" };
    const body = await res.json().catch(() => null);
    const message = isObject(body) ? text(body.message) : null;
    return { kind: "error", status: res.status, message: message ?? fallback };
  } catch {
    return { kind: "error", status: 0, message: OFFLINE_LINE };
  }
}

/** Keep the app: the end date goes away. */
export const keepPackage = (pkg: string, fetchImpl: typeof fetch = fetch): Promise<LifetimeWrite> =>
  outcome(() => fetchImpl(`/api/studio/packages/${encodeURIComponent(pkg)}/keep`, { method: "POST" }), KEEP_FAILED_LINE);

/** Restore an archived app: the end date starts again. */
export const restorePackage = (pkg: string, fetchImpl: typeof fetch = fetch): Promise<LifetimeWrite> =>
  outcome(() => fetchImpl(`/api/studio/packages/${encodeURIComponent(pkg)}/restore`, { method: "POST" }), RESTORE_FAILED_LINE);

/** Whole days from the server `now` to `endsAt`, rounded UP. A date in the past gives 0 or less. */
export function daysLeft(endsAt: string, now: string): number {
  const left = Date.parse(endsAt) - Date.parse(now);
  if (Number.isNaN(left)) return 0;
  // `|| 0` turns -0 into 0.
  return Math.ceil(left / DAY_MS) || 0;
}

/** Which bar to draw. A paused install, an install with no end date and any other state draw none. */
export function barState(install: LifetimeInstall | null | undefined): BarState {
  if (!install) return "none";
  if (install.state === "active" && install.ends_at) return "ends";
  if (install.state === "archived") return "archived";
  return "none";
}

/** "Ends in 3 days", "Ends in 1 day" or "Ends today". */
export function endsText(days: number): string {
  if (days <= 0) return "Ends today";
  return `Ends in ${days} ${days === 1 ? "day" : "days"}`;
}

// The same idiom as `appRequest.ts`: one formatter, in the zone and the locale of the member.
const DATE = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });

/** "Archived. You can restore it until 5 Dec 2026." With no date, the sentence stops at "Archived." */
export function archivedText(restoreUntil: string | null): string {
  const at = restoreUntil ? Date.parse(restoreUntil) : Number.NaN;
  if (Number.isNaN(at)) return "Archived.";
  return `Archived. You can restore it until ${DATE.format(new Date(at))}.`;
}

/** The text of the bar for an install, or null when there is no bar. */
export function barText(install: LifetimeInstall): string | null {
  const state = barState(install);
  if (state === "ends") return endsText(daysLeft(install.ends_at as string, install.now));
  if (state === "archived") return archivedText(install.restore_until);
  return null;
}

/** What the bar remembers while a Keep or a Restore runs. */
export interface FlowState {
  busy: boolean;
  /** Keep worked: the bar goes away. */
  kept: boolean;
  /** The sentence of the gateway for the last refusal, or null. */
  problem: string | null;
}

export interface FlowDeps {
  keep?: (pkg: string) => Promise<LifetimeWrite>;
  restore?: (pkg: string) => Promise<LifetimeWrite>;
  onChange: (state: FlowState) => void;
}

/**
 * The press of Keep and of Restore, with no React. One press runs at a time, so a second press
 * while one runs sends nothing. A success drops the cache of the lifetime and of the package.
 */
export function createLifetimeFlow(pkg: string, deps: FlowDeps) {
  let state: FlowState = { busy: false, kept: false, problem: null };
  const set = (next: FlowState) => { state = next; deps.onChange(state); };
  return {
    state: () => state,
    /** True when the call worked. The caller reads the lifetime again after a Restore. */
    async press(verb: "keep" | "restore"): Promise<boolean> {
      if (state.busy) return false;
      set({ ...state, busy: true, problem: null });
      const send = verb === "keep" ? (deps.keep ?? keepPackage) : (deps.restore ?? restorePackage);
      const end = await send(pkg);
      if (end.kind === "error") {
        set({ ...state, busy: false, problem: end.message });
        return false;
      }
      invalidate(lifetimeKey(pkg));
      invalidate(`studio:package-preview:pkg:${pkg}`);
      set({ busy: false, kept: verb === "keep" ? true : state.kept, problem: null });
      return true;
    },
  };
}
