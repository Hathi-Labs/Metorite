/**
 * POST /api/integrations/mcp/[name]/oauth/start — start the OAuth consent of
 * one MCP server (WS-54 IN-8c, D98).
 *
 * Spec: `project-docs/specs/integrations_console.md` §3.6.4 and §3.6.9. The
 * gateway route (`POST /integrations/mcp/{name}/oauth/start`, IN-8a) needs
 * `integrations:manage`, signs the state, keeps the PKCE verifier in Redis
 * and answers `{url}`, the consent URL of the provider. With
 * `MCP_OAUTH_ENABLED` off it answers 404, and so does this route.
 *
 * The rules, each with a case in `route.test.ts`:
 *
 * 1. **No body goes upstream.** The org and the member come from the session
 *    at the gateway (R5, R11). The request names the server in the path
 *    only, through `refuseBadSegment`.
 * 2. **Same origin only.** A start that another site sends gets 403
 *    (`sameOrigin`, as the agent-connection DELETE routes do).
 * 3. **The URL comes from the gateway, whole.** This route builds no URL and
 *    takes no redirect target from the browser. It passes on the gateway's
 *    `url` only when it is an absolute `https` URL with no user and no
 *    password. Any other answer is a 502. So the route can never become an
 *    open redirect.
 * 4. **A refusal carries a short code.** The gateway's `detail` passes when it
 *    looks like a code, and a 404 that does not (the flag is off) reads
 *    `not_available`. A gateway 3xx is a 502.
 * 5. **Nothing logs the answer.** The consent URL holds the state.
 */
import { NextRequest, NextResponse } from "next/server";
import { refuseBadSegment } from "@/app/api/integrations/safeSegment";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const START_TIMEOUT_MS = 30_000;
const NO_STORE = { "Cache-Control": "no-store" };
const CODE = /^[a-z0-9_]{1,40}$/;
const MAX_URL_CHARS = 4096;

function refuse(detail: string, status: number): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
}

/** The consent URL when it is safe to send a browser there, else null. */
function safeConsentUrl(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > MAX_URL_CHARS) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || !url.hostname || url.username || url.password) return null;
  return url.toString();
}

/** The short code of a gateway refusal. */
function refusalCode(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && CODE.test(detail)) return detail;
  if (status === 404) return "not_available";
  return "failed";
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  if (!sameOrigin(req)) return refuse("cross_origin", 403);
  const { name } = await params;
  const bad = refuseBadSegment(name);
  if (bad) return bad;

  let res: Response;
  try {
    res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/mcp/${encodeURIComponent(name)}/oauth/start`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(START_TIMEOUT_MS),
      },
    );
  } catch {
    return refuse("unreachable", 502);
  }
  if (res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400)) {
    return refuse("failed", 502);
  }
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) return refuse(refusalCode(body, res.status), res.status);
  const url = safeConsentUrl((body as { url?: unknown } | null)?.url);
  if (url === null) return refuse("failed", 502);
  return NextResponse.json({ url }, { headers: NO_STORE });
}
