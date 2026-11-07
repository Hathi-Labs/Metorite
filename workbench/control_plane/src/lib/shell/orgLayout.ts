/**
 * The org default of the shell layout, as the workbench reads it
 * (`package_host.md` §9.1, PKG-17b).
 *
 * `GET /api/shell/layout` proxies the gateway's `GET /shell/layout`. That
 * route answers 404 for an organization outside `PACKAGE_HOST_ORGS`. So the
 * flag is the SERVER's, and this file adds none. A 404, any error and a body
 * that is not a layout all read as "no org default". Then the shell draws
 * exactly what it drew before.
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
 * guest. Every helper is pure and defensive. A shape it does not know is
 * dropped and never thrown.
 *
 * Fence (R7): `orgLayout.test.ts`.
 */
import { cacheKey } from "@/lib/dataCache";
import { STARRABLE, presetById, type PresetId } from "@/lib/shell/presets";
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

/** Read the org default. A 404 (flag off) or any failure is "no org default". */
export async function fetchOrgLayout(): Promise<OrgLayout> {
  try {
    const res = await fetch("/api/shell/layout", { cache: "no-store" });
    if (!res.ok) return EMPTY_ORG_LAYOUT;
    return parseOrgLayout(await res.json());
  } catch {
    return EMPTY_ORG_LAYOUT;
  }
}

/** The org default, from the cache. It is empty while it loads and when the flag is off. */
export function useOrgLayout(enabled: boolean = true): OrgLayout {
  const res = useCachedResource<OrgLayout>(enabled ? orgLayoutKey() : null, fetchOrgLayout, {
    ttl: ORG_LAYOUT_TTL_MS,
    revalidateOnFocus: true,
  });
  return res.data ?? EMPTY_ORG_LAYOUT;
}
