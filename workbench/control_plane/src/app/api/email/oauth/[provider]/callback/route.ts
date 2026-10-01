/**
 * GET /api/email/oauth/{provider}/callback — finish the mailbox OAuth flow.
 *
 * Spec: project-docs/specs/email_app_master_plan.md §10.4.1 (EM-T1a).
 *
 * WHY THE CALLBACK RUNS BEHIND THE SESSION (risk R-4)
 * ---------------------------------------------------
 * This URL is the redirect URI that Microsoft and Google see. The provider
 * sends the browser here with `code` and `state`. The state is signed, but a
 * signed state alone is a bearer value for ten minutes and is not tied to a
 * browser. An attacker could start the flow, get a victim to consent, and the
 * mailbox of the victim would attach to the account of the attacker. So the
 * gateway callback is GATED now. This route attaches the session of the member
 * with `gatewayHeaders()`, and the gateway refuses unless that member is the
 * member in the state.
 *
 * The rest copies the authorize route beside it:
 *
 * - `requireIdentity()` first, so a signed-out caller gets the 401 shape and
 *   never the throw of gatewayHeaders(). EM-T3 owns a kinder page for a session
 *   that ends inside the ten-minute window.
 * - `gatewayHeaders()`, never `serviceHeaders()`. The bearer alone would reach
 *   the gateway as the platform, and the member check would refuse every time.
 * - `redirect: "manual"`, so the 302 of the gateway stays intact, and we pass
 *   on its `Location` ourselves.
 * - Only `code`, `state`, `error` and `error_description` go upstream. The code
 *   is a credential for a short time: this file never logs it and never
 *   retries with it.
 * - The `Location` must name the callback page of the workbench. The gateway
 *   always answers with that path, so any other path is a bug or an attack, and
 *   the member lands on the failure page instead. When `WORKBENCH_PUBLIC_URL`
 *   is set, the origin of the `Location` must also be that origin.
 *
 * ⚠️ NEVER USE `req.nextUrl.origin` AS THE PUBLIC ORIGIN. In production Next
 * runs `next start -p 3001` behind Caddy, and a route handler builds its URL
 * from the bind host, so the origin reads `https://localhost:3001`. Every
 * redirect here is RELATIVE (`/email/oauth/callback?...`) for that reason, and
 * the browser resolves it against the address bar it actually has.
 *
 * Do NOT add `response_mode=form_post` to the authorize URL. The provider would
 * then POST here cross-site, and the browser drops the Lax session cookie on
 * that POST.
 *
 * NOTE ON ROUTING: `src/app/api/email/[...path]/route.ts` also matches this URL.
 * Next resolves a static segment ahead of a catch-all, so this file wins. That
 * matters: the catch-all fetches with the default `redirect: "follow"`, and it
 * would return the JSON of the callback page fetch instead of a redirect.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** Where the workbench renders both the success and the failure of a connect. */
const CALLBACK_PAGE = "/email/oauth/callback";

/** Same containment rule as the authorize route beside this file. */
const PROVIDER_SEGMENT = /^[a-z0-9-]{1,32}$/;

/** The only query parameters that go upstream. */
const FORWARDED_PARAMS = ["code", "state", "error", "error_description"] as const;

const CALLBACK_TIMEOUT_MS = 30_000;

/** A redirect with a RELATIVE Location. See the header on the public origin. */
function relativeRedirect(location: string, status: number): NextResponse {
  return new NextResponse(null, { status, headers: { location } });
}

/** Send a failure to the callback page, because this is a navigation. */
function failed(reason: string): NextResponse {
  const qs = new URLSearchParams({ error: reason }).toString();
  return relativeRedirect(`${CALLBACK_PAGE}?${qs}`, 303);
}

/** The configured public origin of the workbench, or null when unset. */
function workbenchOrigin(): string | null {
  const raw = (process.env.WORKBENCH_PUBLIC_URL || "").trim();
  if (!raw) return null;
  try {
    return new URL(raw).origin;
  } catch {
    return null;
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ provider: string }> }
): Promise<NextResponse> {
  const { provider } = await params;
  if (!PROVIDER_SEGMENT.test(provider)) {
    return failed("unknown_provider");
  }

  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  const headers = await gatewayHeaders();

  const forwarded = new URLSearchParams();
  for (const name of FORWARDED_PARAMS) {
    const value = req.nextUrl.searchParams.get(name);
    if (value) forwarded.set(name, value);
  }
  const qs = forwarded.toString();
  const upstream =
    `${GATEWAY_URL}/email/oauth/${provider}/callback` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    res = await gatewayFetch(upstream, {
      headers,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(CALLBACK_TIMEOUT_MS),
    });
  } catch {
    return failed("gateway_unreachable");
  }

  if (res.status < 300 || res.status >= 400) {
    return failed(`callback_failed_${res.status}`);
  }

  const location = res.headers.get("location");
  if (!location) return failed("callback_no_location");

  let target: URL;
  try {
    target = new URL(location);
  } catch {
    return failed("callback_bad_location");
  }
  if (target.pathname !== CALLBACK_PAGE) {
    return failed("callback_bad_location");
  }
  const expected = workbenchOrigin();
  if (expected !== null && target.origin !== expected) {
    return failed("callback_bad_location");
  }

  return relativeRedirect(target.pathname + target.search, 302);
}
