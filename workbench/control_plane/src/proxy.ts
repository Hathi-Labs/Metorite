/**
 * Sends signed-out *page* navigations to /signin, and answers signed-out API
 * calls with 401.
 *
 * THIS IS NOT THE AUTHORIZATION BOUNDARY, and the list below is not an
 * access-control policy. Next's own guidance is that Proxy "should not be used
 * as a full session management or authorization solution" — it runs before the
 * request is completed, on an optimistic check. The boundary is in each route,
 * next to the fetch: `lib/gateway.ts` refuses to attach the internal bearer
 * without a member, so a route that reaches the gateway has already
 * established who is asking.
 *
 * It is worth being precise about why that matters, because this file used to
 * be load-bearing by accident. `/api/agent`, `/api/settings/`,
 * `/api/integrations/`, `/api/chat/` and `/api/memory/` were all listed as
 * public — not because they were, but because a redirect to an HTML sign-in
 * page is a useless answer to `fetch()`, so exempting them was the quickest
 * way to stop breaking the client. Meanwhile the routes underneath forwarded
 * the internal token with no identity when there was no session, which the
 * gateway reads as the platform acting as itself and grants `*`. The
 * "temporary" exemption list was the only thing standing between an
 * unauthenticated request and agent registration, code-mutation approval, and
 * the integration key inventory — and on those five prefixes it was not
 * standing there at all.
 *
 * So the redirect-vs-401 split is made explicitly here, and authorization is
 * made somewhere that can actually answer it.
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { auth, isAuthEnabled, isAuthConfigured } from "@/auth";
import { isSlotCookieName } from "@/lib/accountSlots";

/** NextAuth's own endpoints, which must stay reachable to sign anybody in. */
const AUTH_ROUTES = "/api/auth";

/**
 * Unauthenticated page routes.
 *
 * ⚠️ `/signin/code` (CP-2d slice 2) is here for the same reason `/signin` is,
 * and leaving it out does not fail safe — it fails *broken*: Auth.js redirects a
 * person who has just asked for an email code to that page, and a person asking
 * for a sign-in code is by definition not signed in, so the redirect below would
 * bounce them straight back to `/signin` and the code could never be entered.
 * The page reads no secret and holds no session; the authorization that matters
 * is `@auth/core`'s own callback, which the typed code is submitted to.
 */
const PUBLIC_PAGES = new Set([
  "/signin",
  "/signin/code",
  "/favicon.ico",
  // WS-17 EM-T3c: where an IT admin lands after approving Metorite in
  // Microsoft. That admin has no Metorite session. The page reads one token
  // from a fixed set, makes no fetch and renders fixed copy only.
  "/oauth/approved",
]);

/**
 * API paths that pass without a session. EXACT matches only, never a prefix.
 *
 * `/api/email/oauth/microsoft/callback` (WS-17 EM-T3c): Microsoft sends an IT
 * admin with no Metorite session here after an admin approval. The route
 * answers that return from constants before it asks for a session.
 * ⚠️ The route stays the boundary for the member path: every other request,
 * a `code` and `state` included, still meets `requireIdentity()` there, so a
 * signed-out member still gets 401 and never reaches the gateway. Fences:
 * `proxy.test.ts` and the callback's `route.test.ts`.
 *
 * `/api/health` (`navigation_shell.md` §7.3): the probe of the shell's update
 * notice, so the sign-in page can say "updating" too. It answers up or down
 * and a build id, and reads nothing private. Fence: `proxy.test.ts`.
 */
const PUBLIC_API_PATHS = new Set([
  "/api/email/oauth/microsoft/callback",
  "/api/health",
]);

/**
 * The account switcher's slots end with the session (MT-1k slice A2, rule 4).
 *
 * ⚠️ Enforced HERE, on every request, and not beside each sign-out button.
 * Four paths end a session: the switcher's "Sign out of all accounts", a plain
 * `signOut()` (the sign-up page, the old footer), Auth.js's own sign-out page,
 * and an expiry. Only the first cleared the slots, so after the other three the
 * next person at that computer could switch into the accounts left behind
 * (security review, 2026-10-07). A request that carries slot cookies and no
 * live session now gets them deleted, whichever way the session ended.
 *
 * `auth()` runs only when a slot cookie is present, so a browser that never
 * used the switcher pays nothing. Fence: `proxy.test.ts`.
 */
async function sweepOrphanSlots(req: NextRequest, res: NextResponse): Promise<NextResponse> {
  const slots = (req.cookies?.getAll?.() ?? []).filter((c) => isSlotCookieName(c.name));
  if (slots.length === 0 || (await auth())) return res;
  for (const c of slots) {
    res.cookies.set(c.name, "", {
      httpOnly: true,
      secure: c.name.startsWith("__Host-"),
      sameSite: "lax",
      path: "/",
      maxAge: 0,
    });
  }
  return res;
}

export async function proxy(req: NextRequest) {
  return sweepOrphanSlots(req, await gate(req));
}

async function gate(req: NextRequest): Promise<NextResponse> {
  // The laptop case, and ONLY the laptop case, runs open. This used to be
  // `!isAuthEnabled` where that merely meant "no client id configured", so a
  // production box whose auth env went missing served every route to anyone
  // (D33.1). `isAuthEnabled` is now false only when NODE_ENV is non-production
  // as well — see `auth.ts`.
  if (!isAuthEnabled) return NextResponse.next();

  const { pathname } = req.nextUrl;

  // Enforcing, but with nothing to enforce with: a production deployment that
  // lost (or never got) its auth configuration. Refuse everything, loudly.
  //
  // Not a redirect to /signin: that page cannot sign anybody in without a
  // provider, so it would be an infinite loop presented as a login screen —
  // which reads to an operator as "auth is working". 503 says the true thing,
  // and says it to health checks too.
  if (!isAuthConfigured) {
    return NextResponse.json(
      { error: "Authentication is not configured on this deployment" },
      { status: 503 },
    );
  }

  if (pathname.startsWith(AUTH_ROUTES) || pathname.startsWith("/_next")) {
    return NextResponse.next();
  }

  // ── Per-tenant workspace hostnames: WITHDRAWN (D51, 2026-08-24) ───────────
  // MT-1f slice 1's host-parse branch lived here for one day. The owner
  // withdrew subdomain workspaces entirely — one door (`app.<domain>`), the
  // organization made explicit in the UI, and the multi-org workspace CHOICE
  // to be carried as a session claim when MT-1g builds it (never the Host
  // header: a request hostname is request input, R11). The reserved-slug
  // protection that shipped with the slice STAYS — `lib/subdomain.ts` still
  // owns RESERVED_LABELS/SLUG_RE and the signup gate enforces them.

  if (PUBLIC_PAGES.has(pathname)) return NextResponse.next();
  if (PUBLIC_API_PATHS.has(pathname)) return NextResponse.next();

  if (await auth()) return NextResponse.next();

  // An API caller wants a status code it can branch on, not a login page. The
  // shape matches lib/gateway's UNAUTHENTICATED so the client sees one thing.
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Sign in to continue" }, { status: 401 });
  }

  // The WHOLE link comes back after the sign-in, query included. An
  // org-aware link (`lib/orgLink.ts`) carries `?org=` and a record id there,
  // so the path alone opened the wrong page. Auth.js's own redirect check
  // keeps the callback on this origin. Fence: `proxy.test.ts`.
  const url = new URL("/signin", req.url);
  url.searchParams.set("callbackUrl", `${pathname}${req.nextUrl.search}`);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
