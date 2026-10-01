/**
 * GET /api/email/oauth/{provider}/authorize — start the mailbox OAuth flow.
 *
 * WHY THIS ROUTE EXISTS
 * ---------------------
 * Connecting a mailbox needs a *browser navigation*: the gateway answers 302 to
 * Microsoft/Google and the consent screen has to land in the address bar. Both
 * Connect buttons used to navigate straight at the gateway host
 * (`${NEXT_PUBLIC_GATEWAY_URL}/email/oauth/…`), on the theory that the BFF
 * cannot carry a redirect. That request carries no Bearer and no X-User-Email —
 * session cookies live on the workbench origin, not `api.*`, and a navigation
 * cannot add custom headers — so once default-deny landed (57ec82d9, 2026-07-29)
 * `require_authenticated(public=PUBLIC_ROUTES)` 401'd it for every user before
 * the handler ran. Nobody could connect an account.
 *
 * The repair is NOT to make `/email/oauth/{provider}/authorize` public. The
 * handler binds the new mailbox to `user_id`, so an anonymous authorize leg
 * taking its identity from a query parameter would let anyone attach a mailbox
 * to somebody else's account — a worse bug than the one being fixed.
 *
 * So the navigation lands here instead. This runs server-side, has the session,
 * and calls the SAME gated gateway route with `gatewayHeaders()` — the internal
 * bearer plus the signed-in member's `X-User-Email`. `redirect: "manual"` keeps
 * the 302 intact instead of following it server-side (which would fetch the
 * provider's consent HTML with our own credentials and hand the user a page with
 * no cookies for it); we read `Location` and re-issue the redirect ourselves, so
 * the consent screen is reached by the browser, as a navigation, with the
 * provider's own cookies.
 *
 * `user_email` is deliberately NOT forwarded. Identity is asserted in the header
 * by gatewayHeaders(); a query parameter naming a different person must not even
 * reach the gateway from here. (The gateway also prefers `user.email` over that
 * parameter now — see routes/email/transport/oauth.py.)
 *
 * NOTE ON ROUTING: `src/app/api/email/[...path]/route.ts` also matches this URL.
 * Next resolves static/dynamic segments ahead of a catch-all, so this file wins.
 * That matters: the catch-all fetches with the default `redirect: "follow"` and
 * would silently return the provider's login HTML through the proxy.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** Where the workbench renders both the success and the failure of a connect. */
const CALLBACK_PAGE = "/email/oauth/callback";

/**
 * The provider is interpolated into an upstream path, so it is held to a plain
 * lowercase segment — the same containment reasoning as the `[...path]` proxy's
 * traversal guard, which attaches the same internal bearer. The gateway remains
 * the authority on *which* providers exist (it 400s an unknown one); this only
 * guarantees the segment cannot be spelled to reach a sibling gateway route.
 */
const PROVIDER_SEGMENT = /^[a-z0-9-]{1,32}$/;

const AUTHORIZE_TIMEOUT_MS = 30_000;

/**
 * Hand a failure to the callback page rather than rendering JSON into the
 * address bar — this is a navigation, and the page already knows how to explain
 * an OAuth failure (it is where the gateway's own callback errors land).
 *
 * ⚠️ The Location is RELATIVE. Do not build it from `req.nextUrl.origin`: in
 * production Next runs behind Caddy on `localhost:3001`, so a route handler's
 * origin is the bind host, not the public origin (EM-T1a fix round 1, F3).
 */
function failed(reason: string): NextResponse {
  const qs = new URLSearchParams({ error: reason }).toString();
  return new NextResponse(null, {
    status: 303,
    headers: { location: `${CALLBACK_PAGE}?${qs}` },
  });
}

/** The gateway's `detail`, when it refused with one worth showing. */
async function refusalReason(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = (body as { detail?: unknown })?.detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
  } catch {
    // Non-JSON refusal; the status is all we have.
  }
  return `authorize_failed_${res.status}`;
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ provider: string }> }
): Promise<NextResponse> {
  const { provider } = await params;
  if (!PROVIDER_SEGMENT.test(provider)) {
    return failed(`unknown_provider`);
  }

  // Resolve the member BEFORE reaching for the bearer, so a signed-out caller is
  // told to sign in rather than being handed gatewayHeaders()' throw as a 502.
  // proxy.ts already answers signed-out /api/* with the same 401 shape, so this
  // is the floor rather than the usual answer — and it must stay a refusal: a
  // connect flow with nobody behind it has no mailbox owner.
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  const headers = await gatewayHeaders();

  // Only `redirect_after` and `login_hint` are forwarded; see the header
  // comment on `user_email`. `login_hint` is a HINT for the provider's sign-in
  // page (the reconnect banner sends the mailbox address). The gateway drops a
  // value that does not parse as one address, and the callback still binds the
  // member of the session, so it can never choose whose mailbox is attached
  // (EM-T3a item 3, EM-T3b).
  const forwarded = new URLSearchParams();
  const redirectAfter = req.nextUrl.searchParams.get("redirect_after");
  if (redirectAfter) forwarded.set("redirect_after", redirectAfter);
  const loginHint = req.nextUrl.searchParams.get("login_hint")?.trim();
  if (loginHint && loginHint.length <= 254) forwarded.set("login_hint", loginHint);
  const qs = forwarded.toString();

  const upstream =
    `${GATEWAY_URL}/email/oauth/${provider}/authorize` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    res = await gatewayFetch(upstream, {
      headers,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(AUTHORIZE_TIMEOUT_MS),
    });
  } catch {
    return failed("gateway_unreachable");
  }

  if (res.status < 300 || res.status >= 400) {
    return failed(await refusalReason(res));
  }

  const location = res.headers.get("location");
  if (!location) return failed("authorize_no_location");

  // The provider consent URL is built by the gateway from configured provider
  // constants, so this is a sanity check rather than a trust boundary — but a
  // redirect target that is not an absolute https URL is a bug worth seeing
  // instead of a 500 out of NextResponse.redirect.
  let target: URL;
  try {
    target = new URL(location);
  } catch {
    return failed("authorize_bad_location");
  }
  if (target.protocol !== "https:" && target.protocol !== "http:") {
    return failed("authorize_bad_location");
  }

  return NextResponse.redirect(target.toString(), 302);
}
