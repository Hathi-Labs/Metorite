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
import { GATEWAY_URL, headersActingAs } from "@/lib/gateway";
import { workspaceHostSlug, workspaceRedirect } from "@/lib/subdomain";

/** NextAuth's own endpoints, which must stay reachable to sign anybody in. */
const AUTH_ROUTES = "/api/auth";

/**
 * The domain per-tenant workspace hostnames hang off (WS-29 MT-1f).
 *
 * Only ever used to *recognise* `<slug>.<domain>` and to build the one host
 * everybody is sent back to. It is not a tenant claim and grants nothing.
 *
 * Read per REQUEST, not once at module load, for the reason `/signup`'s page
 * carries `force-dynamic`: an env value captured at module scope freezes at
 * build/boot, and the owner's flip is an env-plus-restart act whose two halves
 * must land together.
 */
function workspaceBaseDomain(): string {
  return process.env.WORKSPACE_BASE_DOMAIN ?? "metorite.com";
}

/**
 * The caller's organization slug, as the GATEWAY resolves it — MT-1f slice 1's
 * one source for "which workspace is this person's".
 *
 * ⚠️ **Three alternatives were rejected in writing** (`saas_multitenancy.md`
 * §11 MT-1f, *"where slice 1 learns the caller's organization"*), and the
 * reasons matter more than the choice:
 *
 * * **a browser-visible header or any client-supplied hint** — R11 by name; the
 *   acting tenant is never taken from request input, and a `Host` header IS
 *   request input, which is the whole reason this function exists;
 * * **a claim in the NextAuth JWT** — `workbench/AGENTS.md`'s standing reason
 *   for keeping permissions out of the token applies unchanged: *a JWT outlives
 *   an access change*, so a tenant minted at sign-in survives an off-boarding;
 * * **a new gateway route or a Next-side org cache** — root `CLAUDE.md` §5's
 *   second way to do an existing thing. `GET /auth/me` is what `AccessProvider`
 *   already renders the whole shell from; `organization.slug` is the same
 *   answer resolved by the same server-side code.
 *
 * **Uncached, deliberately.** It is reached only when the flag is on, the host
 * carries a workspace slug AND a session exists — zero requests today, and
 * after the flip only subdomain traffic; `app.metorite.com` never reaches it.
 * A cached tenant answer is a stale tenant answer, which is the failure this
 * ticket exists to prevent. If slice 2 gives workspace hosts real traffic,
 * caching is slice 2's decision with its own fence.
 *
 * Returns `null` on every failure, which {@link workspaceRedirect} treats as a
 * MISMATCH — failing towards the neutral apex, never towards serving a tenant
 * hostname to somebody we could not place.
 */
async function resolvedOrgSlug(email: string): Promise<string | null> {
  try {
    const res = await fetch(`${GATEWAY_URL}/auth/me`, {
      // The ONE internal-bearer seam. This module never mints a bearer of its
      // own — `gateway.test.ts` sweeps this file for exactly that.
      headers: headersActingAs(email),
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    const me = (await res.json()) as { organization?: { slug?: unknown } };
    const slug = me?.organization?.slug;
    return typeof slug === "string" && slug ? slug.trim().toLowerCase() : null;
  } catch {
    return null;
  }
}

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
const PUBLIC_PAGES = new Set(["/signin", "/signin/code", "/favicon.ico"]);

export async function proxy(req: NextRequest) {
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

  // ── WS-29 MT-1f slice 1 · per-tenant workspace hostnames ──────────────────
  //
  // THIS IS THE ONLY READER OF THE REQUEST HOST IN THE BROWSER TIER, and
  // `subdomain.test.ts` fails the build on a second one — precisely: on a second
  // `headers().get("host")`, `nextUrl.host`/`nextUrl.hostname` or
  // `x-forwarded-host` anywhere under `src/`. It is deliberately NOT a claim
  // about `window.location.host`, which is the browser reading the address bar
  // it is already on (`app/whatsapp/lib/callAudio.ts` does, legitimately) and
  // decides nothing about tenancy. A REQUEST hostname is request input: read in
  // two places it becomes two opinions about which tenant you are looking at,
  // and the losing one is a cross-tenant surface.
  //
  // Placed AFTER the `/_next` and `/api/auth` passthroughs so static assets and
  // the sign-in machinery on a workspace host are untouched, and BEFORE
  // `PUBLIC_PAGES` so a signed-in person on the wrong workspace's `/signin` is
  // moved too. It runs at all only when the flag is exactly `"true"` and the
  // host carries a real slug, so an un-flipped deployment (every deployment
  // today) does no extra work whatsoever — done-when 3.
  //
  // The signed-out branch performs NO LOOKUP OF ANY KIND (owner ruling B4): an
  // existing and an invented slug must be indistinguishable, and the only way
  // that survives a refactor is never having asked. `signedIn` is therefore
  // resolved first and the gateway call is guarded on it.
  //
  // ⚠️ It also REDIRECTS rather than falling through to a host-local `/signin`
  // (done-when 6 as amended 2026-08-24). Under B2 the Auth.js cookies stay
  // host-only on the apex, so a sign-in begun on `acme.<domain>` writes its
  // `state`/`pkce` there while B3's `AUTH_URL` pin returns the callback to
  // `app.<domain>` — the sign-in cannot complete, and signed-out is the only
  // state a workspace host is in today. `/api/auth/**` still passes through
  // above (NextAuth's endpoints must stay reachable on every host), but no page
  // served from a workspace host can now lead a person into it.
  const baseDomain = workspaceBaseDomain();
  const hostSlug = workspaceHostSlug(
    process.env.SUBDOMAIN_WORKSPACE_ENABLED,
    req.headers.get("host"),
    baseDomain,
  );
  if (hostSlug !== null) {
    const email = (await auth())?.user?.email ?? "";
    const location = workspaceRedirect({
      hostSlug,
      signedIn: Boolean(email),
      callerSlug: email ? await resolvedOrgSlug(email) : null,
      baseDomain,
      pathWithQuery: `${pathname}${req.nextUrl.search}`,
    });
    // 302 and nothing else: no body, no header, and a Location built from the
    // fixed apex host plus the caller's own path — so the answer NAMES NO
    // ORGANIZATION, neither the host's nor the caller's (done-when 5), and is
    // byte-identical for a real and an invented slug (done-when 6): a location
    // assembled from a FIXED host cannot echo the hostname the caller typed.
    if (location) return NextResponse.redirect(location, 302);
  }

  if (PUBLIC_PAGES.has(pathname)) return NextResponse.next();

  if (await auth()) return NextResponse.next();

  // An API caller wants a status code it can branch on, not a login page. The
  // shape matches lib/gateway's UNAUTHENTICATED so the client sees one thing.
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Sign in to continue" }, { status: 401 });
  }

  const url = new URL("/signin", req.url);
  url.searchParams.set("callbackUrl", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
