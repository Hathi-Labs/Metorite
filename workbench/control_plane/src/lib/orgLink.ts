/**
 * Org-aware links (MT-1k, `saas_multitenancy.md` "Org-aware links",
 * owner 2026-10-10).
 *
 * A copied link carries `?org=<organization uuid>`. When a member opens it
 * while another of their accounts is active, the page switches to the account
 * of that organization first. When no signed-in account belongs to it, the
 * page says so instead of failing with an empty or a wrong page.
 *
 * ⚠️ `?account=` is NOT free. Email reads it as a mailbox id
 * (`app/email/lib/emailLink.ts`), so the organization goes in `?org=`.
 *
 * Security: the parameter only CHOOSES among accounts that this browser has
 * already signed in to. A switch is the existing same-origin
 * `POST /api/accounts/switch` (`accountsServer.ts`), never a GET and never the
 * proxy. The gateway still binds the tenant from the active session alone
 * (R11). Every path a switch or a sign-in lands on passes
 * {@link safeLocalPath}, so a link cannot redirect off the site.
 *
 * Pure, so node-env vitest holds it. Fence: `orgLink.test.ts`.
 */
import type { Accounts } from "@/lib/accountSwitch";

/** The query parameter that names the organization of a link. */
export const ORG_PARAM = "org";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** True for an organization id in the shape the gateway issues (a UUID). */
export function isOrgId(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value);
}

function sameId(a: string | null | undefined, b: string): boolean {
  return typeof a === "string" && a.toLowerCase() === b.toLowerCase();
}

/** A base no real link can name, to parse a relative path against. */
const BASE = "https://org-link.invalid";

/**
 * The path itself when it is a same-origin relative path, else `null`.
 *
 * It must start with one `/`. `//evil.example` and `/\evil.example` are
 * scheme-relative to the browser, and a control byte is dropped by the URL
 * parser, so `/\t/evil.example` becomes `//evil.example`. Each one is refused.
 */
export function safeLocalPath(path: unknown): string | null {
  if (typeof path !== "string" || !path.startsWith("/")) return null;
  if (path.startsWith("//") || path.includes("\\")) return null;
  if (/[\u0000-\u001f\u007f]/.test(path)) return null;
  try {
    if (new URL(path, BASE).origin !== BASE) return null;
  } catch {
    return null;
  }
  return path;
}

/** The same path, query and hash, without the `org` parameter. */
export function withoutOrg(pathAndQuery: string): string {
  const url = new URL(pathAndQuery, BASE);
  url.searchParams.delete(ORG_PARAM);
  return `${url.pathname}${url.search}${url.hash}`;
}

/**
 * Stamp the organization onto a link that a member copies to share. An
 * unknown or malformed id leaves the link as it was.
 */
export function withOrg(link: string, orgId: string | null | undefined): string {
  if (!isOrgId(orgId)) return link;
  const hashAt = link.indexOf("#");
  const head = hashAt < 0 ? link : link.slice(0, hashAt);
  const hash = hashAt < 0 ? "" : link.slice(hashAt);
  const sep = head.includes("?") ? "&" : "?";
  return `${head}${sep}${ORG_PARAM}=${encodeURIComponent(orgId)}${hash}`;
}

/** The sign-in page, set to come back to `link` after the sign-in. */
export function signInUrl(link: string, adding: boolean): string {
  const params = new URLSearchParams();
  if (adding) params.set("add", "1");
  const back = safeLocalPath(link);
  if (back) params.set("callbackUrl", back);
  const query = params.toString();
  return query ? `/signin?${query}` : "/signin";
}

export type OrgLinkDecision =
  /** No `org` parameter, or nobody is signed in: the proxy owns that case. */
  | { kind: "none" }
  /** A malformed id, or the active account's own org: remove it, go on. */
  | { kind: "strip" }
  /** The org is not the active one: read the other accounts first. */
  | { kind: "load" }
  /** Another signed-in account belongs to the org: switch to it. */
  | { kind: "switch"; slot: number; email: string }
  /**
   * No signed-in account belongs to the org. `canAdd` is true when the
   * switcher is on, so "Sign in to that account" keeps this one in a slot.
   */
  | { kind: "notice"; canAdd: boolean };

export interface OrgLinkInput {
  /** The raw `org` value of the URL, or `null`. */
  param: string | null;
  authenticated: boolean;
  /** The active account's organization id, from `/auth/me`. */
  activeOrgId: string | null | undefined;
  /** The other accounts with their org ids, or `null` before the read. */
  accounts: Accounts | null;
}

/** What a page does with the `org` parameter of its URL. */
export function decideOrgLink(input: OrgLinkInput): OrgLinkDecision {
  const { param, authenticated, activeOrgId, accounts } = input;
  if (param === null) return { kind: "none" };
  if (!isOrgId(param)) return { kind: "strip" };
  if (!authenticated) return { kind: "none" };
  if (sameId(activeOrgId, param)) return { kind: "strip" };
  if (accounts === null) return { kind: "load" };
  if (accounts.enabled) {
    const match = accounts.others.find((o) => sameId(o.organization_id, param));
    if (match) return { kind: "switch", slot: match.slot, email: match.email };
  }
  return { kind: "notice", canAdd: accounts.enabled };
}
