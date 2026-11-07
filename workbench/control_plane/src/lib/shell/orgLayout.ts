/**
 * The org default of the shell layout, as the workbench reads it
 * (`package_host.md` §9.1, PKG-17b).
 *
 * `GET /api/shell/layout` proxies the gateway's `GET /shell/layout`. That
 * route answers 404 for an organization outside `PACKAGE_HOST_ORGS`. So the
 * flag is the SERVER's, and this file adds none. A 404 and a body that is not
 * a layout read as "no org default". Then the shell draws exactly what it drew
 * before. Any other failure throws, so the cache keeps the last good answer.
 *
 * The org default has a browser copy per account, as the member's layout has
 * (`shellCache.ts`). The copy draws the first frame, and the read confirms it
 * or replaces it. A 404 or an empty answer clears the copy.
 *
 * With no copy yet, the shell holds its skeleton for a member with null pins
 * until the read answers, for `ORG_LAYOUT_WAIT_MS` at most
 * (`orgLayoutSettled`). So that member does not see every app first.
 *
 * The default is a FALLBACK. It applies to a member with no stored choice
 * and to nobody else (`shellLayout` in `presets.ts`). It follows the
 * precedence of `shell_layout.resolve_layout` on the gateway:
 *
 * - `pins`: the member's own list wins, an empty list too. A member with
 *   `null` pins takes the pins of the role default.
 * - `preset`: the member's own answered preset wins. Else the role default.
 *   Else `roleDefault` of the client.
 * - A pane that the member does not hold stays hidden, because `starredHrefs`
 *   reads `visibleSections` alone.
 *
 * The highest role that has an entry wins: owner, admin, manager, member,
 * guest. Every parse helper is pure and defensive. A shape it does not know is
 * dropped and never thrown.
 *
 * Fence (R7): `orgLayout.test.ts`.
 */
import { useEffect, useMemo } from "react";

import { cacheKey } from "@/lib/dataCache";
import { STARRABLE, presetById, type PresetId } from "@/lib/shell/presets";
import {
  clearCachedOrgLayout,
  readCachedOrgLayoutRaw,
  writeCachedOrgLayout,
} from "@/lib/shell/shellCache";
import { useCachedResource } from "@/lib/useCachedResource";

/** The roles a person holds, the highest first (`shell_layout.ROLE_ORDER`). */
export const ROLE_ORDER = ["owner", "admin", "manager", "member", "guest"] as const;
export type OrgRole = (typeof ROLE_ORDER)[number];

/** The default of one role. `null` leaves that part to the system. */
export interface OrgRoleLayout {
  preset: PresetId | null;
  pins: string[] | null;
}

export interface OrgLayout {
  roles: Partial<Record<OrgRole, OrgRoleLayout>>;
}

export const EMPTY_ORG_LAYOUT: OrgLayout = { roles: {} };

/** The default that applies to one member. */
export interface OrgDefault {
  preset: PresetId | null;
  pins: string[] | null;
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Read a body into an org layout. A shape it does not know is dropped. Never throws. */
export function parseOrgLayout(body: unknown): OrgLayout {
  if (!isRecord(body) || !isRecord(body.roles)) return EMPTY_ORG_LAYOUT;
  const roles: Partial<Record<OrgRole, OrgRoleLayout>> = {};
  for (const role of ROLE_ORDER) {
    const raw = Object.prototype.hasOwnProperty.call(body.roles, role) ? body.roles[role] : undefined;
    if (!isRecord(raw)) continue;
    const preset = typeof raw.preset === "string" ? (presetById(raw.preset)?.id ?? null) : null;
    let pins: string[] | null = null;
    if (Array.isArray(raw.pins)) {
      pins = [];
      for (const pane of raw.pins) {
        if (typeof pane === "string" && STARRABLE.has(pane) && !pins.includes(pane)) pins.push(pane);
      }
    }
    if (preset === null && pins === null) continue;
    roles[role] = { preset, pins };
  }
  return { roles };
}

/**
 * The default for a member who holds `roles`: the entry of the highest role
 * that has one. `null` when no role has an entry. A custom role has no entry
 * of its own, as on the gateway.
 */
export function orgDefaultFor(layout: OrgLayout | null | undefined, roles: readonly string[]): OrgDefault | null {
  if (!layout) return null;
  const held = new Set(roles.map((r) => String(r).toLowerCase()));
  for (const role of ROLE_ORDER) {
    const entry = held.has(role) ? layout.roles[role] : undefined;
    if (entry) return { preset: entry.preset, pins: entry.pins ? [...entry.pins] : null };
  }
  return null;
}

export const ORG_LAYOUT_CACHE = "shell/layout";
export const orgLayoutKey = (): string => cacheKey(ORG_LAYOUT_CACHE);
/** An admin's change reaches a member within this time. */
export const ORG_LAYOUT_TTL_MS = 60_000;

/** True when the layout has no entry for any role. */
export const isEmptyOrgLayout = (layout: OrgLayout): boolean => Object.keys(layout.roles).length === 0;

/**
 * Read the org default.
 *
 * - A 404 (the host is off for this org) is "no org default".
 * - A 200 with a body it can read is the layout. A shape it does not know
 *   reads as "no org default".
 * - Every other status, a body that is not JSON and a network fault THROW.
 *   `useCachedResource` then keeps the last good answer (`applyError`), so a
 *   gateway restart never replaces a good default with "no default".
 */
export async function fetchOrgLayout(): Promise<OrgLayout> {
  const res = await fetch("/api/shell/layout", { cache: "no-store" });
  if (res.status === 404) return EMPTY_ORG_LAYOUT;
  if (!res.ok) throw new Error(`org layout ${res.status}`);
  return parseOrgLayout(await res.json());
}

/** The account's browser copy of the org default, or undefined. A bad or empty copy is no copy. */
export function readCachedOrgLayout(scope: string | null | undefined): OrgLayout | undefined {
  const raw = readCachedOrgLayoutRaw(scope);
  if (raw === undefined) return undefined;
  const layout = parseOrgLayout(raw);
  return isEmptyOrgLayout(layout) ? undefined : layout;
}

/**
 * Keep the server's answer in the browser copy. An answer with a default is
 * written. A 404 or an empty answer clears the copy, so a default that an
 * admin removed does not come back on the next page load.
 */
export function keepOrgLayoutCopy(scope: string | null | undefined, answer: OrgLayout): void {
  if (isEmptyOrgLayout(answer)) clearCachedOrgLayout(scope);
  else writeCachedOrgLayout(scope, answer);
}

/**
 * How much the shell knows of the org default.
 *
 * - `pending`: no answer, no failure and no browser copy yet.
 * - `copy`: the browser copy draws, and the read may still be out.
 * - `answered`: the read answered or failed. A failure with no copy and no
 *   earlier answer is "no org default", as before PKG-17b.
 */
export type OrgLayoutState = "pending" | "copy" | "answered";

export function orgLayoutState(a: {
  data: OrgLayout | undefined;
  error: string | null;
  copy: OrgLayout | undefined;
}): OrgLayoutState {
  if (a.data !== undefined || a.error !== null) return "answered";
  return a.copy !== undefined ? "copy" : "pending";
}

/**
 * The longest time the shell waits for the org default, from the first
 * render. The sidebar keeps its skeleton for this time at most. It is less
 * than `SHELL_WAIT_MS`, so the skeleton never runs out first.
 */
export const ORG_LAYOUT_WAIT_MS = 1_500;

/**
 * Whether the shell may settle, as far as the org default goes (the hold).
 *
 * The shell holds only while ALL of these are true:
 *
 * - the member's stored `pins` are null, or the member read has not answered
 *   (`undefined`). A member with their own pins never waits.
 * - the org default is `pending`: no browser copy and no answer.
 * - `elapsedMs` is less than `ORG_LAYOUT_WAIT_MS`.
 *
 * Without the hold, a member with null pins saw every held app and then the
 * default stars. After the wait the shell settles with what it has.
 */
export function orgLayoutSettled(a: {
  storedPins: readonly string[] | null | undefined;
  orgState: OrgLayoutState;
  elapsedMs: number;
}): boolean {
  if (Array.isArray(a.storedPins)) return true;
  if (a.orgState !== "pending") return true;
  return a.elapsedMs >= ORG_LAYOUT_WAIT_MS;
}

export interface OrgLayoutRead {
  /** The default to draw: the answer, else the browser copy, else none. */
  layout: OrgLayout;
  state: OrgLayoutState;
}

/**
 * What the shell draws from. The answer wins, a failed read keeps the copy,
 * and with neither there is no org default. Pure, so the fence can drive it.
 */
export function orgLayoutRead(a: {
  data: OrgLayout | undefined;
  error: string | null;
  copy: OrgLayout | undefined;
}): OrgLayoutRead {
  return { layout: a.data ?? a.copy ?? EMPTY_ORG_LAYOUT, state: orgLayoutState(a) };
}

/**
 * The org default for this account. The browser copy draws the first frame,
 * and the read through the cache confirms it or replaces it.
 */
export function useOrgLayout(enabled: boolean, scope: string | null | undefined): OrgLayoutRead {
  const res = useCachedResource<OrgLayout>(enabled ? orgLayoutKey() : null, fetchOrgLayout, {
    ttl: ORG_LAYOUT_TTL_MS,
    revalidateOnFocus: true,
  });
  const copy = useMemo(() => (enabled ? readCachedOrgLayout(scope) : undefined), [enabled, scope]);
  useEffect(() => {
    if (enabled && scope && res.data !== undefined) keepOrgLayoutCopy(scope, res.data);
  }, [enabled, scope, res.data]);
  if (!enabled) return { layout: EMPTY_ORG_LAYOUT, state: "answered" };
  return orgLayoutRead({ data: res.data, error: res.error, copy });
}
