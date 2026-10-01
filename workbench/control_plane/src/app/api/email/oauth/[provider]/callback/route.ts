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
 * - The `Location` must be on OUR origin. The gateway always answers with the
 *   callback page of the workbench, so any other origin is a bug or an attack,
 *   and the member lands on the failure page instead.
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

/** Send a failure to the callback page, because this is a navigation. */
function failed(req: NextRequest, reason: string): NextResponse {
  const url = new URL(CALLBACK_PAGE, req.nextUrl.origin);
  url.searchParams.set("error", reason);
  return NextResponse.redirect(url, 303);
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ provider: string }> }
): Promise<NextResponse> {
  const { provider } = await params;
  if (!PROVIDER_SEGMENT.test(provider)) {
    return failed(req, "unknown_provider");
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
    return failed(req, "gateway_unreachable");
  }

  if (res.status < 300 || res.status >= 400) {
    return failed(req, `callback_failed_${res.status}`);
  }

  const location = res.headers.get("location");
  if (!location) return failed(req, "callback_no_location");

  let target: URL;
  try {
    target = new URL(location);
  } catch {
    return failed(req, "callback_bad_location");
  }
  if (target.origin !== req.nextUrl.origin) {
    return failed(req, "callback_bad_location");
  }

  return NextResponse.redirect(target.toString(), 302);
}
