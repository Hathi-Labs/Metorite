// ── The single door from the BFF to the gateway ────────────────────────────
//
// Every /api/** route forwards to the FastAPI gateway with the internal bearer
// token plus the signed-in member's identity. This module is the only place
// that may mint that token; `gateway.test.ts` fails if a route file reads
// GATEWAY_INTERNAL_TOKEN again.
//
// WHY IT IS THE ONLY PLACE
// ------------------------
// The header block used to be copy-pasted into ~70 route files, each shaped
// like this:
//
//     const h = { Authorization: `Bearer ${INTERNAL_TOKEN}` };
//     const session = await auth();
//     if (session?.user?.email) h["X-User-Email"] = session.user.email;
//     return h;                      // ← no session: the bearer travels alone
//
// and this module's own predecessor carried the comment "an unauthenticated
// forward is resolved to no-access by the gateway, which is the correct
// outcome." That premise was false, and everything downstream rested on it.
// `acb_auth/deps.py` §1b reads a bearer-matched call WITHOUT identity headers
// as the platform acting as ITSELF and grants SERVICE_ACCESS — `*`, every
// permission there is. Omitting the identity did not degrade the caller to
// anonymous; it PROMOTED them past every check, including the
// `require_permission` guards on /admin that `api/admin/[...path]` explicitly
// delegates to.
//
// The distinction that matters is between ASSERTING an identity and OMITTING
// one. Asserting is fine — a token holder could assert anything anyway, which
// is what makes §1b defensible. Omitting has to fail closed, because that is
// what an unauthenticated HTTP request to Next.js looks like by the time it
// arrives here. This is the same bug that was fixed twice in /memory: once at
// the scope guard, once in lib/memory.ts. Fixing it per route is how it came
// back a second time; one door is what stops the third.
//
// Note that `proxy.ts`'s public-path list is NOT the boundary and never was.
// Next's own guidance is that Proxy "should not be used as a full session
// management or authorization solution" — it is an optimistic redirect for
// page navigations. Authorization belongs here, next to the fetch.
//
// THE RULE
// --------
//   gatewayHeaders()  needs a person, and throws NoIdentityError without one.
//   serviceHeaders()  is bearer-only, and is the sole way to obtain that. It
//                     takes a written reason, so every identity-free call is
//                     an argument someone made on purpose.
//
// The role header only feeds the gateway's LEGACY require_role() checks. Real
// authorization is resolved server-side from the org tables keyed on the email
// (packages/acb_auth/acb_auth/access.py), so a stale role header cannot grant
// access the member does not have.

import { NextResponse } from "next/server";
import { auth, isAuthEnabled } from "@/auth";

export const GATEWAY_URL = process.env.GATEWAY_BASE_URL ?? "http://127.0.0.1:8000";

const INTERNAL_TOKEN =
  process.env.GATEWAY_INTERNAL_TOKEN ??
  process.env.LITELLM_MASTER_KEY ??
  "sk-local-dev-change-me";

/**
 * Bootstrap admin list. Retained because a deployment whose access tables have
 * not been migrated yet still needs someone to be an admin — see spec §7. Once
 * roles are assigned in /settings/organization, the database is the source of truth
 * and the gateway upgrades the role itself.
 */
const EXECUTIVE_EMAILS = new Set(
  (process.env.EXECUTIVE_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean)
);

export interface Identity {
  email: string;
  role: "executive" | "employee";
}

/**
 * The identity used when SSO is not configured at all, so the app still works
 * on a laptop.
 *
 * Gated on `isAuthEnabled`, which since D33.1 is false **only** when there is no
 * provider AND `NODE_ENV !== "production"`. The old gate was "no client id set",
 * which meant a production deployment that lost its auth env handed this
 * identity — a real, privileged member — to every anonymous caller. A
 * misconfigured production box now gets `null` here (and a 503 at `proxy.ts`),
 * so the failure is a refusal rather than a silent grant.
 */
const DEV_IDENTITY: Identity = { email: "dev@fracktal.in", role: "employee" };

/**
 * Thrown when a call needs a person and there is nobody signed in.
 *
 * Deliberately an exception rather than a null return: the previous helper
 * returned usable headers either way, so forgetting to check was both silent
 * and privilege-granting. A throw cannot be forgotten — the route either
 * handles it or 500s, and 500 is a safe wrong answer where 200 was not.
 */
export class NoIdentityError extends Error {
  constructor() {
    super("No signed-in member to act as");
    this.name = "NoIdentityError";
  }
}

/** The signed-in member, or null when there is nobody to act as. */
export async function currentIdentity(): Promise<Identity | null> {
  try {
    const session = await auth();
    const email = session?.user?.email;
    if (email) {
      return {
        email,
        role: EXECUTIVE_EMAILS.has(email.toLowerCase()) ? "executive" : "employee",
      };
    }
  } catch {
    // auth() throws outside a request context (build-time evaluation, tests).
    // Fall through: no session is no session, however we failed to find one.
  }
  return isAuthEnabled ? null : DEV_IDENTITY;
}

/**
 * Internal bearer + identity headers for an ALREADY-VERIFIED member.
 *
 * For library code that was handed an email by a route which established it
 * (`lib/memory.ts`). Routes themselves want {@link gatewayHeaders}, which
 * resolves the session rather than trusting an argument.
 *
 * @throws {NoIdentityError} on a blank email — the case that has to fail
 * closed, since a blank one previously produced a bearer travelling alone.
 */
export function headersActingAs(
  email: string,
  extra: Record<string, string> = {}
): Record<string, string> {
  const who = (email ?? "").trim();
  if (!who) throw new NoIdentityError();
  return {
    ...extra,
    // AFTER the spread, deliberately. `extra` exists so a route can set
    // Content-Type; it must not become a second answer to "who is asking",
    // which is settled here and nowhere else.
    Authorization: `Bearer ${INTERNAL_TOKEN}`,
    "X-User-Email": who,
    "X-User-Role": EXECUTIVE_EMAILS.has(who.toLowerCase()) ? "executive" : "employee",
  };
}

/**
 * Internal bearer + the signed-in member's identity headers.
 *
 * @throws {NoIdentityError} when nobody is signed in. A caller that genuinely
 * has no person behind it wants {@link serviceHeaders} and has to say so.
 */
export async function gatewayHeaders(
  extra: Record<string, string> = {}
): Promise<Record<string, string>> {
  const me = await currentIdentity();
  if (!me) throw new NoIdentityError();
  return headersActingAs(me.email, extra);
}

/**
 * The platform acting as ITSELF — no person, and therefore SERVICE_ACCESS at
 * the gateway. The only way to obtain a bearer without an identity.
 *
 * `reason` is unused at runtime and required at the type level: it exists so
 * every identity-free call carries, at the call site, the argument for why
 * this request is on behalf of nobody. Reviewing a diff that adds one should
 * mean reading that sentence and disagreeing with it if it is wrong.
 *
 * Legitimate uses are calls whose answer is identical for everyone and which
 * disclose nothing about any member — the model catalogue's "which providers
 * have keys configured" check, health probes. Anything scoped to a person, and
 * anything that writes, is not one of these.
 */
export function serviceHeaders(
  reason: string,
  extra: Record<string, string> = {}
): Record<string, string> {
  void reason;
  return { Authorization: `Bearer ${INTERNAL_TOKEN}`, ...extra };
}

/** 401 body shared by every route, so the client can branch on one shape. */
export const UNAUTHENTICATED = { error: "Sign in to continue" } as const;
export type Unauthenticated = typeof UNAUTHENTICATED;

export function unauthenticated(): NextResponse<Unauthenticated> {
  return NextResponse.json(UNAUTHENTICATED, { status: 401 });
}

/**
 * The signed-in member, or a 401 to return directly:
 *
 *     const me = await requireIdentity();
 *     if (me instanceof NextResponse) return me;
 *     // me.email is now a real person
 *
 * For handlers that need the email itself. Handlers that only forward should
 * use {@link proxyToGateway}, which does this internally.
 */
export async function requireIdentity(): Promise<
  Identity | NextResponse<Unauthenticated>
> {
  const me = await currentIdentity();
  return me ?? unauthenticated();
}

/**
 * Forward a request to the gateway as the signed-in member and pass the
 * response straight through. 401s when there is nobody signed in.
 *
 * Errors reaching the gateway are the caller's to handle — a route that knows
 * what its own unavailability means to the UI can answer better than a shared
 * helper guessing at 502.
 */
export async function proxyToGateway(
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  let headers: Record<string, string>;
  try {
    headers = await gatewayHeaders({
      "Content-Type": "application/json",
      ...((init.headers as Record<string, string>) ?? {}),
    });
  } catch (err) {
    if (err instanceof NoIdentityError) return unauthenticated();
    throw err;
  }
  const upstream = `${GATEWAY_URL}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(upstream, { ...init, headers, cache: "no-store" });
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: {
      "Content-Type": res.headers.get("content-type") ?? "application/json",
    },
  });
}
