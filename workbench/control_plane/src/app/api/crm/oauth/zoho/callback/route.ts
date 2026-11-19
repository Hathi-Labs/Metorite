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
 * **Zoho Books (API-2, `api_connections.md` §2.11).** Zoho knows ONE redirect
 * URI for Metorite's client, this one. A Books consent sends a state that
 * starts with the public mark `zb.`. This route sends such a state to the
 * gateway's `/integrations/api/zoho/callback`, and every other state to the
 * CRM callback. The mark proves nothing: the gateway checks the MAC of the
 * Books purpose, so a forged mark only changes where the state goes. A Books
 * reply may name `/integrations` with one of three parameters, and nothing
 * else.
 *
 * Fence: `route.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

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

/** The public mark of a Zoho Books state (`connections/api/connect.py`). */
const BOOKS_STATE_MARK = "zb.";

/** The refusal codes of the Books callback that the console may name. */
const BOOKS_KNOWN_ERRORS = new Set([
  "access_denied",
  "invalid_state",
  "untrusted_host",
  "zoho_not_configured",
  "pending_unavailable",
]);

const CALLBACK_TIMEOUT_MS = 30_000;

function relativeRedirect(location: string): NextResponse {
  return new NextResponse(null, { status: 302, headers: { location } });
}

function failed(code: string): NextResponse {
  const safe = KNOWN_ERRORS.has(code) ? code : "failed";
  return relativeRedirect(`/crm?connect_error=${safe}`);
}

function booksFailed(code: string): NextResponse {
  const safe = BOOKS_KNOWN_ERRORS.has(code) ? code : "failed";
  return relativeRedirect(`/integrations?api_connect_error=${safe}`);
}

/** Split a relative Location into its path and its one query pair. */
function onePair(raw: string): { path: string; name: string; value: string } | null {
  // Only a path may follow. An absolute URL is never the gateway's shape.
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  let target: URL;
  try {
    target = new URL(raw, "http://placeholder.invalid");
  } catch {
    return null;
  }
  if (target.origin !== "http://placeholder.invalid") return null;
  const names = [...target.searchParams.keys()];
  if (names.length !== 1) return null;
  const [name] = names;
  return { path: target.pathname, name, value: target.searchParams.get(name) ?? "" };
}

/** Is this one of the three Location shapes of the Books callback? */
function allowedBooksLocation(raw: string): string | null {
  const pair = onePair(raw);
  if (!pair || pair.path !== "/integrations") return null;
  const { name, value } = pair;
  if (name === "api_connected" && value === "zoho_books") {
    return "/integrations?api_connected=zoho_books";
  }
  if (name === "api_connect_error" && /^[a-z0-9_]{1,40}$/.test(value)) {
    return `/integrations?api_connect_error=${value}`;
  }
  if (name === "api_pick" && /^[A-Za-z0-9_-]{16,64}$/.test(value)) {
    return `/integrations?api_pick=${value}`;
  }
  return null;
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
  const books = (req.nextUrl.searchParams.get("state") ?? "").startsWith(BOOKS_STATE_MARK);
  const path = books ? "/integrations/api/zoho/callback" : "/crm/oauth/zoho/callback";
  const upstream = `${GATEWAY_URL}${path}` + (qs ? `?${qs}` : "");
  const fail = books ? booksFailed : failed;

  let res: Response;
  try {
    // ⚠️ ONE attempt: `{ retry: false }`. By default a GET through
    // `gatewayFetch` retries on a stale socket (H-194), and the consent code
    // is single-use: a retry after the first attempt reached the gateway
    // would turn a good connect into a failure. The seam stays, so the H-194
    // fence holds. A throw here becomes `/crm?connect_error=failed`.
    res = await gatewayFetch(
      upstream,
      {
        headers,
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(CALLBACK_TIMEOUT_MS),
      },
      { retry: false },
    );
  } catch {
    return fail("failed");
  }

  if (res.status >= 300 && res.status < 400) {
    const location = res.headers.get("location");
    const allow = books ? allowedBooksLocation : allowedLocation;
    const safe = location ? allow(location) : null;
    return safe ? relativeRedirect(safe) : fail("failed");
  }
  // A 400 (`invalid_state`, `untrusted_host`), a 403, a 404 or a 409
  // (`mode_is_native`): the page shows words for the code, or a generic line.
  return fail(await refusalCode(res));
}
