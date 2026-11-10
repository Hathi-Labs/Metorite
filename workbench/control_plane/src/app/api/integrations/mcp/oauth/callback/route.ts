/**
 * GET /api/integrations/mcp/oauth/callback — finish an MCP OAuth consent
 * (WS-54 IN-8a, D98).
 *
 * Spec: `project-docs/specs/integrations_console.md` §3.6.4. This is the one
 * redirect URI of every MCP consent, and of the CIMD document
 * (`/oauth/mcp-client.json`). The provider sends the admin's browser here with
 * `code`, `state` and, from a server that says so, `iss` (RFC 9207). The
 * gateway callback (`/integrations/mcp/oauth/callback`) needs the session of
 * the admin who started the flow, and it answers with a 302 to
 * `/integrations?mcp_connected=<name>` or
 * `/integrations?mcp_connect_error=<code>`. With `MCP_OAUTH_ENABLED` off it
 * answers 404, and this route sends the generic failure.
 *
 * The rules of the Zoho callback (`app/api/crm/oauth/zoho/callback`):
 *
 * - `requireIdentity()` first, then `gatewayHeaders()`, never the service
 *   bearer. The gateway refuses unless the member in the state is the member
 *   of the session.
 * - `redirect: "manual"`, and ONE attempt, because the code is single-use.
 * - Only four query names go upstream. The code is a credential for a short
 *   time. This file never logs the query.
 * - Every Location is RELATIVE and names `/integrations` with one of two
 *   parameters. A gateway reply that does not fit becomes the generic failure.
 *
 * Fence: `route.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The only query parameters that go upstream. */
const FORWARDED_PARAMS = ["code", "state", "error", "iss"] as const;

/** The refusal codes of the gateway callback that the console names. */
const KNOWN_ERRORS = new Set(["access_denied", "invalid_state", "invalid_issuer"]);

const CALLBACK_TIMEOUT_MS = 30_000;
const PAGE = "/integrations";

function relativeRedirect(location: string): NextResponse {
  return new NextResponse(null, { status: 302, headers: { location } });
}

function failed(code: string): NextResponse {
  const safe = KNOWN_ERRORS.has(code) ? code : "failed";
  return relativeRedirect(`${PAGE}?mcp_connect_error=${safe}`);
}

/** Is this one of the two Location shapes the gateway callback sends? */
function allowedLocation(raw: string): string | null {
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  let target: URL;
  try {
    target = new URL(raw, "http://placeholder.invalid");
  } catch {
    return null;
  }
  if (target.pathname !== PAGE) return null;
  const names = [...target.searchParams.keys()];
  if (names.length !== 1) return null;
  const [name] = names;
  const value = target.searchParams.get(name) ?? "";
  // The MCP server name rule of the gateway (`connections/mcp.py` NAME_RE).
  if (name === "mcp_connected" && /^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$/.test(value)) {
    return `${PAGE}?mcp_connected=${value}`;
  }
  if (name === "mcp_connect_error" && /^[a-z0-9_]{1,40}$/.test(value)) {
    return `${PAGE}?mcp_connect_error=${value}`;
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
  const upstream = `${GATEWAY_URL}/integrations/mcp/oauth/callback` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    // ⚠️ ONE attempt: the consent code is single-use, and the pending flow
    // is taken once. A retry would turn a good connect into a failure.
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
  // A 400 (`invalid_state`, `invalid_issuer`), a 403, a 404 (the flag is
  // off) or a 503: the console shows words for the code, or a generic line.
  return failed(await refusalCode(res));
}
