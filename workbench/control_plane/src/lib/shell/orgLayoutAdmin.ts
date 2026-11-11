/**
 * The admin side of the org default of the shell layout (`package_host.md`
 * §9.1, PKG-17c-1). Pure client code. It has no React and no page. The page
 * is PKG-17c-2.
 *
 * `readOrgLayoutRaw` is NOT `fetchOrgLayout` (`orgLayout.ts`). That fetcher
 * hides a fault as "no default" and writes the browser copy. A form that
 * saves a whole document must tell a fault from an empty default. So this
 * read keeps three answers apart and writes no cache.
 *
 * - `ok`: a 200 with a body it can read. `empty` is true when no role has an
 *   entry. The page asks the admin before it saves from an empty read.
 * - `off`: a 404. The org is not in `PACKAGE_HOST_ORGS`.
 * - `fault`: a 5xx, a 401, a 403, a network error or a body it cannot read.
 *   The page never allows a Save after a fault.
 *
 * Fence (R7): `orgLayoutAdmin.test.ts`.
 */
import { put } from "@/lib/dataCache";
import {
  ROLE_ORDER,
  isEmptyOrgLayout,
  orgLayoutKey,
  parseOrgLayout,
  type OrgLayout,
  type OrgRole,
  type OrgRoleLayout,
} from "@/lib/shell/orgLayout";
import { STARRABLE, presetById } from "@/lib/shell/presets";

/**
 * The most pins one role takes. It mirrors `MAX_PINS` in
 * `apps/services/gateway/gateway/routes/admin/me.py`, which
 * `gateway/package_host/shell_layout.py` reads for `RoleLayout.pins`.
 */
export const MAX_PINS = 64;

export type OrgLayoutRead =
  | { kind: "ok"; layout: OrgLayout; empty: boolean }
  | { kind: "off" }
  | { kind: "fault" };

export type OrgLayoutSave = { kind: "saved"; layout: OrgLayout } | { kind: "error"; status: number; reason: string };

/** Read the org default, and keep a fault apart from "no default". Never throws. */
export async function readOrgLayoutRaw(): Promise<OrgLayoutRead> {
  try {
    const res = await fetch("/api/shell/layout", { cache: "no-store" });
    if (res.status === 404) return { kind: "off" };
    if (!res.ok) return { kind: "fault" };
    const body: unknown = await res.json();
    if (typeof body !== "object" || body === null || Array.isArray(body)) return { kind: "fault" };
    if (typeof (body as { roles?: unknown }).roles !== "object" || (body as { roles?: unknown }).roles === null) {
      return { kind: "fault" };
    }
    const layout = parseOrgLayout(body);
    return { kind: "ok", layout, empty: isEmptyOrgLayout(layout) };
  } catch {
    return { kind: "fault" };
  }
}

/** The body of one role for the gateway: known panes only, no repeat, at most `MAX_PINS`. */
function roleBody(entry: OrgRoleLayout): OrgRoleLayout {
  const preset = entry.preset && presetById(entry.preset) ? entry.preset : null;
  let pins: string[] | null = null;
  if (Array.isArray(entry.pins)) {
    pins = [];
    for (const pane of entry.pins) {
      if (typeof pane === "string" && STARRABLE.has(pane) && !pins.includes(pane)) pins.push(pane);
    }
    pins = pins.slice(0, MAX_PINS);
  }
  return { preset, pins };
}

/**
 * The PUT body. It holds the FULL roles map, so a role the admin did not touch
 * goes back unchanged. A role with nothing in it is left out. `updatedBy` and
 * `updatedAt` are never sent, because the gateway model forbids extra keys.
 */
export function toBody(layout: OrgLayout): { roles: Partial<Record<OrgRole, OrgRoleLayout>> } {
  const roles: Partial<Record<OrgRole, OrgRoleLayout>> = {};
  for (const role of ROLE_ORDER) {
    const entry = layout.roles[role];
    if (!entry) continue;
    const clean = roleBody(entry);
    if (clean.preset === null && clean.pins === null) continue;
    roles[role] = clean;
  }
  return { roles };
}

/** Set the default of one role. An entry with nothing in it removes the role. */
export function setRole(layout: OrgLayout, role: OrgRole, entry: OrgRoleLayout): OrgLayout {
  const roles = { ...layout.roles };
  const clean = roleBody(entry);
  if (clean.preset === null && clean.pins === null) delete roles[role];
  else roles[role] = clean;
  return { roles };
}

/** Remove the default of one role. */
export function resetRole(layout: OrgLayout, role: OrgRole): OrgLayout {
  const roles = { ...layout.roles };
  delete roles[role];
  return { roles };
}

/** The reason text of a gateway answer. FastAPI puts it in `detail`. */
function reasonOf(body: unknown, status: number): string {
  if (typeof body === "object" && body !== null) {
    const b = body as { detail?: unknown; error?: unknown };
    if (typeof b.detail === "string" && b.detail) return b.detail;
    if (Array.isArray(b.detail)) {
      const msgs = b.detail
        .map((d) => (typeof d === "object" && d !== null && typeof (d as { msg?: unknown }).msg === "string" ? (d as { msg: string }).msg : ""))
        .filter(Boolean);
      if (msgs.length) return msgs.join(", ");
    }
    if (typeof b.error === "string" && b.error) return b.error;
  }
  return `HTTP ${status}`;
}

/** Save the whole document. Never throws. */
export async function saveOrgLayout(layout: OrgLayout): Promise<OrgLayoutSave> {
  let res: Response;
  try {
    res = await fetch("/api/shell/layout", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(toBody(layout)),
      cache: "no-store",
    });
  } catch {
    return { kind: "error", status: 0, reason: "The server did not answer." };
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) return { kind: "error", status: res.status, reason: reasonOf(body, res.status) };
  return { kind: "saved", layout: parseOrgLayout(body) };
}

/**
 * Put the saved default into the cache key of the shell, so the shell shows it
 * within its TTL and not after the next read. The page calls it after a save.
 */
export function refreshShellDefault(layout: OrgLayout): void {
  put(orgLayoutKey(), layout);
}
