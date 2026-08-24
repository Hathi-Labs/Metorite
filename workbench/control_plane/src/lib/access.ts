// ── Org access control — client-side types and helpers ───────────────────
//
// Spec: project-docs/specs/org_access_control.md
//
// The gateway resolves permissions and returns *outcomes* — a list of allowed
// feature slugs and runnable agent names. This module deliberately does NOT
// re-implement the wildcard matching rule: two implementations of an
// authorization rule is one too many, and the one in the browser is the one
// that drifts. Everything here is a lookup against the server's answer.
//
// Nothing here is a security boundary. Hiding a nav item is a courtesy; the
// gateway's require_permission() is what actually refuses the request.

import { CENTERS } from "@/lib/centers";

export type Access = {
  email: string;
  user_id: string;
  authenticated: boolean;
  is_active: boolean;
  organization: { id?: string; slug?: string; display_name?: string };
  roles: string[];
  legacy_role: string;
  /** Feature slugs this member may reach, e.g. ["chat", "email"]. */
  features: string[];
  /**
   * Feature slugs this member may NOT reach, each with the permission it
   * needs. The half that makes a hidden pane explainable: without it, "you
   * lack the grant", "the slug was never registered" and "the gateway is
   * down" all render as the same nothing, and the only way to tell them apart
   * is to ask somebody with database access.
   */
  features_denied: Array<{ slug: string; permission: string }>;
  /** Agent names this member may run. */
  agents: string[];
  /** Raw granted patterns (admin screens display these verbatim). */
  permissions: string[];
  /**
   * Concrete capabilities the server RESOLVED to yes for this member, e.g.
   * ["apps:publish", "workflows:publish"]. Use this — not `permissions` —
   * for "may they do X": an owner holds "*", never the literal string.
   */
  capabilities: string[];
  denied: string[];
  is_admin: boolean;
};

/** What an unresolved / signed-out viewer gets: nothing. */
export const NO_ACCESS: Access = {
  email: "",
  user_id: "",
  authenticated: false,
  is_active: false,
  organization: {},
  roles: [],
  legacy_role: "employee",
  features: [],
  features_denied: [],
  agents: [],
  permissions: [],
  capabilities: [],
  denied: [],
  is_admin: false,
};

// ── href → feature slug ───────────────────────────────────────────────────
//
// Longest-prefix match, so "/settings/models" resolves to `models` rather
// than being swallowed by a shorter "/settings" entry. Kept in sync with the
// feature_catalog table (infra/postgres/130_org_access_control.sql).

const HREF_FEATURES: ReadonlyArray<[string, string]> = [
  // Departmental Centers — each landing page is gated by its own feature
  // (feature_catalog rows seeded in 140_center_features.sql).
  ...CENTERS.map((c): [string, string] => [`/centers/${c.slug}`, c.feature]),
  ["/chat", "chat"],
  ["/email", "email"],
  ["/inbox", "email"],
  ["/whatsapp", "whatsapp"],
  ["/memory", "memory"],
  ["/tasks", "tasks"],
  ["/notes", "notes"],
  // The Sales Center's pipeline module. Gated on its own slug, not on
  // `center.sales`: a Center is a projection, and its modules carry their own
  // grants (specs/crm_app.md §5).
  ["/crm", "crm"],
  // Projects — the People Center's work-management module, projected into
  // every other Center as (app + scope). Gated on its own slug for the same
  // reason as /crm; note this feature gates DATA as well as the pane, so the
  // grant model in the API is the real boundary and this is the courtesy half
  // (specs/project_management_app.md §5).
  ["/projects", "projects"],
  ["/people", "people"],
  ["/dashboard", "dashboard"],
  ["/observability", "observability"],
  ["/artifacts", "artifacts"],
  ["/workflows", "workflows"],
  ["/settings/models", "models"],
  ["/agents", "agents"],
  ["/approvals", "approvals"],
  ["/integrations", "integrations"],
  ["/apis", "integrations"],
  ["/build/agents", "build.agents"],
  ["/build/apps", "build.apps"],
];

/**
 * The feature slug guarding a route, or null when the route is unguarded.
 *
 * An always-allowed route answers `null` even when a shorter prefix would
 * match it: `/people/me` sits under `/people`, and the whole point of D-PC-15
 * is that the directory's grant does not reach a person's own row.
 */
export function featureForPath(pathname: string): string | null {
  if (isAlwaysAllowed(pathname)) return null;
  let best: string | null = null;
  let bestLength = 0;
  for (const [prefix, slug] of HREF_FEATURES) {
    if (
      (pathname === prefix || pathname.startsWith(prefix + "/")) &&
      prefix.length > bestLength
    ) {
      best = slug;
      bestLength = prefix.length;
    }
  }
  return best;
}

/**
 * Routes every signed-in member may reach regardless of feature grants —
 * the sign-in page, the access-denied page itself (a redirect loop is worse
 * than an over-permissive 200 on a page that renders "no access"), and the
 * personal settings a member needs to manage their own account.
 */
const ALWAYS_ALLOWED = [
  "/",
  "/signin",
  "/settings",
  "/settings/profile",
  // "Your access" — the page that explains why a pane is missing. It has to be
  // reachable by exactly the person who cannot reach things, so gating it on a
  // feature would make it useless in the only situation it exists for.
  "/access",
  // "My profile". `featureForPath` matches by PREFIX, so /people/me would
  // otherwise inherit the directory's `people` slug — which is
  // `is_default false`, and is what made an ordinary colleague unable to open
  // their own record (WS-28g-2 / D-PC-15). The gateway serves it from a router
  // with no feature dependency; this is the same answer on the client side.
  "/people/me",
];

export function isAlwaysAllowed(pathname: string): boolean {
  return ALWAYS_ALLOWED.includes(pathname);
}

// ── Lookups ───────────────────────────────────────────────────────────────

export function canUseFeature(access: Access, slug: string): boolean {
  return access.features.includes(slug);
}

export function canRunAgent(access: Access, name: string): boolean {
  return access.agents.includes(name);
}

/**
 * Whether the member holds a concrete capability, e.g. "workflows:publish".
 * A lookup against the server's resolved answer — hiding the control is a
 * courtesy, require_permission() on the route is the actual boundary.
 */
export function hasCapability(access: Access, name: string): boolean {
  return access.capabilities.includes(name);
}

export function canSeePath(access: Access, pathname: string): boolean {
  if (isAlwaysAllowed(pathname)) return true;
  // Admin surfaces are gated on the resolved admin flag, not a feature slug —
  // they have no nav pane of their own.
  if (
    // `/settings/organization` is the admin destination under D49 — members &
    // roles, seat assignments and branding as tabs. `/settings/members` keeps
    // its entry because the member-detail route lives under it and old links
    // point at it.
    pathname.startsWith("/settings/organization") ||
    pathname.startsWith("/settings/members") ||
    pathname.startsWith("/settings/roles") ||
    pathname.startsWith("/settings/groups")
  ) {
    return access.is_admin;
  }
  const slug = featureForPath(pathname);
  if (!slug) return true;
  return canUseFeature(access, slug);
}

/**
 * The outcome of one resolve attempt — **authoritative or not**.
 *
 * Spec: `project-docs/specs/launch_surface.md` §8.2 / LS-5.
 *
 * This used to be a bare `Promise<Access>` that mapped *every* failure to
 * `NO_ACCESS`: a 502 from a restarting gateway, a dropped connection, a
 * timeout, an aborted fetch. `AccessProvider` re-resolves every 120 seconds, so
 * one blip on a long-lived tab emptied the sidebar and the member concluded
 * they had been signed out. They had not been — nobody had said anything about
 * their access at all.
 *
 * The distinction is the fix, and it belongs here rather than at the call site,
 * because the call site cannot see the status code:
 *
 * - `ok`           — the server answered. This is the truth, including when the
 *                    truth is "you hold nothing".
 * - `unauthorized` — the server said **no** (401/403). Also authoritative:
 *                    clear the access.
 * - `unavailable`  — nobody said anything. Network error, 5xx, malformed body.
 *                    **Keep whatever was last known** and try again later.
 *
 * `aborted` is separate from all three: it is *our own* teardown (a React
 * effect cleanup, a navigation), so it must not be reported as a failure or a
 * StrictMode double-mount would look like an outage.
 */
export type AccessResult =
  | { kind: "ok"; access: Access }
  | { kind: "unauthorized" }
  | { kind: "unavailable"; reason: string }
  | { kind: "aborted" };

/** Normalize a payload: a gateway predating a list field means "nothing", not undefined. */
function normalize(raw: Partial<Access>): Access {
  return {
    ...NO_ACCESS,
    ...raw,
    features: raw.features ?? [],
    features_denied: raw.features_denied ?? [],
    agents: raw.agents ?? [],
    permissions: raw.permissions ?? [],
    capabilities: raw.capabilities ?? [],
    denied: raw.denied ?? [],
  };
}

/**
 * Resolve the caller's access, reporting WHETHER the answer is authoritative.
 *
 * Never throws. See {@link AccessResult} for why the three outcomes are not
 * collapsed into one.
 */
export async function resolveAccess(signal?: AbortSignal): Promise<AccessResult> {
  let res: Response;
  try {
    res = await fetch("/api/auth/me", { signal, cache: "no-store" });
  } catch (err) {
    if (signal?.aborted || (err as { name?: string })?.name === "AbortError") {
      return { kind: "aborted" };
    }
    return { kind: "unavailable", reason: "network" };
  }
  if (res.status === 401 || res.status === 403) return { kind: "unauthorized" };
  // Everything else that is not ok is the SERVER being unable to answer, not an
  // answer of "no". A 500 from the gateway is not a revoked permission.
  if (!res.ok) return { kind: "unavailable", reason: `http_${res.status}` };
  try {
    return { kind: "ok", access: normalize((await res.json()) as Partial<Access>) };
  } catch {
    // A 200 we cannot parse tells us nothing either — same treatment as a 502.
    return { kind: "unavailable", reason: "malformed" };
  }
}

/**
 * Fetch the caller's access. Never throws — failures resolve to NO_ACCESS.
 *
 * ⚠️ **Prefer {@link resolveAccess}** anywhere the answer will be held across
 * time. This wrapper throws away the distinction between "the server said no"
 * and "the server said nothing", which is exactly the information a re-resolve
 * needs (§8.2). Kept for one-shot callers that genuinely have no prior value to
 * preserve, where failing closed is the right default.
 */
export async function fetchAccess(signal?: AbortSignal): Promise<Access> {
  const result = await resolveAccess(signal);
  return result.kind === "ok" ? result.access : NO_ACCESS;
}
