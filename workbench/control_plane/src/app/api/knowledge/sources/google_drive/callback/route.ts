/**
 * GET /api/knowledge/sources/google_drive/callback — finish the Drive consent
 * (WS-61 KC-1a, `knowledge_curation.md` §7.1).
 *
 * The owner registers this URL in the Google console. Google sends the
 * browser here with `code` and `state`. The gateway callback
 * (`/knowledge/sources/google_drive/callback`) needs the session of the
 * member who started the flow, and it answers with a 302 to
 * `/knowledge?source_connected=google_drive` or
 * `/knowledge?source_error=<code>`.
 *
 * The generic proxy at `../../../[...path]` cannot serve this URL. It follows
 * redirects, so the browser would get the JSON of a page fetch, and its GET
 * may retry the single-use code. A static segment wins over a catch-all in
 * Next, so this file runs first.
 *
 * Rules, copied from the CRM callback under `app/api/crm/oauth/zoho`:
 *
 * - `requireIdentity()` first, then `gatewayHeaders()`, never the service
 *   bearer. The gateway refuses unless the member in the state is the member
 *   of the session.
 * - `redirect: "manual"`, so this route passes on the 302 itself.
 * - Only three query names go upstream. The code is a credential for a short
 *   time. This file never logs the query and never retries with it.
 * - Every Location is RELATIVE and names `/knowledge` with one of two
 *   parameters. Behind Caddy the bind host is `localhost:3001`, so an absolute
 *   URL would send the member to the wrong origin. A gateway reply that does
 *   not fit becomes `/knowledge?source_error=<code>` from a fixed list.
 *
 * Fence: `route.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The only query parameters that go upstream. */
const FORWARDED_PARAMS = ["code", "state", "error"] as const;

/** The JSON refusal codes of the gateway callback that the page may name. */
const KNOWN_ERRORS = new Set(["invalid_state", "google_not_configured"]);

const CALLBACK_TIMEOUT_MS = 30_000;

function relativeRedirect(location: string): NextResponse {
  return new NextResponse(null, { status: 302, headers: { location } });
}

function failed(code: string): NextResponse {
  const safe = KNOWN_ERRORS.has(code) ? code : "failed";
  return relativeRedirect(`/knowledge?source_error=${safe}`);
}

/** Is this one of the two Location shapes the gateway callback sends? */
function allowedLocation(raw: string): string | null {
  // Only a path may follow. An absolute URL is never the gateway's shape.
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  let target: URL;
  try {
    target = new URL(raw, "http://placeholder.invalid");
  } catch {
    return null;
  }
  if (target.host !== "placeholder.invalid" || target.pathname !== "/knowledge") return null;
  const names = [...target.searchParams.keys()];
  if (names.length !== 1) return null;
  const [name] = names;
  const value = target.searchParams.get(name) ?? "";
  if (name === "source_connected" && value === "google_drive") {
    return "/knowledge?source_connected=google_drive";
  }
  if (name === "source_error" && /^[a-z0-9_]{1,64}$/.test(value)) {
    return `/knowledge?source_error=${value}`;
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
  const upstream =
    `${GATEWAY_URL}/knowledge/sources/google_drive/callback` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    // ⚠️ ONE attempt: `{ retry: false }`. By default a GET through
    // `gatewayFetch` retries on a stale socket (H-194), and the consent code
    // is single-use. A throw here becomes `/knowledge?source_error=failed`.
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
    return failed("failed");
  }

  if (res.status >= 300 && res.status < 400) {
    const location = res.headers.get("location");
    const safe = location ? allowedLocation(location) : null;
    return safe ? relativeRedirect(safe) : failed("failed");
  }
  // A 400 (`invalid_state`), a 404 (a flag off), a 503
  // (`google_not_configured`): the page shows words for the code, or a
  // generic line.
  return failed(await refusalCode(res));
}
