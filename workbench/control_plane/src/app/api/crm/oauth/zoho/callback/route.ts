/**
 * GET /api/crm/oauth/zoho/callback — finish the Zoho consent (WS-53 CRM-Z7).
 *
 * The owner registers this URL in the Zoho console. Zoho sends the browser
 * here with `code` and `state`. The gateway callback (`/crm/oauth/zoho/callback`)
 * needs the session of the admin who started the flow, and it answers with a
 * 302 to `/crm?connected=zoho` or `/crm?connect_error=<code>`.
 *
 * The generic proxy at `../../[...path]` cannot serve this URL. It follows
 * redirects, so the browser would get the JSON of the CRM page fetch. A static
 * segment wins over a catch-all in Next, so this file runs first.
 *
 * Rules, copied from the email callback under `app/api/email/oauth`:
 *
 * - `requireIdentity()` first, then `gatewayHeaders()`, never the service
 *   bearer. The gateway refuses unless the member in the state is the member
 *   of the session.
 * - `redirect: "manual"`, so this route passes on the 302 itself.
 * - Only five query names go upstream. The code is a credential for a short
 *   time. This file never logs the query and never retries with it.
 * - Every Location is RELATIVE and names `/crm` with one of two parameters.
 *   Behind Caddy the bind host is `localhost:3001`, so an absolute URL would
 *   send the admin to the wrong origin. A gateway reply that does not fit
 *   becomes `/crm?connect_error=<code>` from a fixed list.
 *
 * Fence: `route.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The only query parameters that go upstream. */
const FORWARDED_PARAMS = ["code", "state", "error", "location", "accounts-server"] as const;

/** The refusal codes the CRM page has words for (`app/crm/lib/connect.ts`). */
const KNOWN_ERRORS = new Set([
  "access_denied",
  "invalid_state",
  "untrusted_host",
  "mode_is_native",
  "zoho_not_configured",
]);

const CALLBACK_TIMEOUT_MS = 30_000;

function relativeRedirect(location: string): NextResponse {
  return new NextResponse(null, { status: 302, headers: { location } });
}

function failed(code: string): NextResponse {
  const safe = KNOWN_ERRORS.has(code) ? code : "failed";
  return relativeRedirect(`/crm?connect_error=${safe}`);
}

/** Is this the one Location shape the gateway callback sends? */
function allowedLocation(raw: string): string | null {
  // Only a path may follow. An absolute URL is never the gateway's shape.
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  let target: URL;
  try {
    target = new URL(raw, "http://placeholder.invalid");
  } catch {
    return null;
  }
  if (target.pathname !== "/crm") return null;
  const names = [...target.searchParams.keys()];
  if (names.length !== 1) return null;
  const [name] = names;
  const value = target.searchParams.get(name) ?? "";
  if (name === "connected" && value === "zoho") return "/crm?connected=zoho";
  if (name === "connect_error" && /^[a-z0-9_]{1,40}$/.test(value)) {
    return `/crm?connect_error=${value}`;
  }
  return null;
}

/** The `detail` code of a JSON refusal, or an empty string. */
async function refusalCode(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  const headers = await gatewayHeaders();

  const forwarded = new URLSearchParams();
  for (const name of FORWARDED_PARAMS) {
    const value = req.nextUrl.searchParams.get(name);
    if (value) forwarded.set(name, value);
  }
  const qs = forwarded.toString();
  const upstream = `${GATEWAY_URL}/crm/oauth/zoho/callback` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    // ⚠️ A plain `fetch`, ONE attempt. `gatewayFetch` retries on a stale
    // socket (H-194), and the consent code is single-use: a retry after the
    // first attempt reached the gateway would turn a good connect into a
    // failure. A throw here becomes `/crm?connect_error=failed`.
    res = await fetch(upstream, {
      headers,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(CALLBACK_TIMEOUT_MS),
    });
  } catch {
    return failed("failed");
  }

  if (res.status >= 300 && res.status < 400) {
    const location = res.headers.get("location");
    const safe = location ? allowedLocation(location) : null;
    return safe ? relativeRedirect(safe) : failed("failed");
  }
  // A 400 (`invalid_state`, `untrusted_host`), a 403, a 404 or a 409
  // (`mode_is_native`): the page shows words for the code, or a generic line.
  return failed(await refusalCode(res));
}
