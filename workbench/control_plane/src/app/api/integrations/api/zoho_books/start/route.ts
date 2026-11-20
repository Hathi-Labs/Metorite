/**
 * POST /api/integrations/api/zoho_books/start — start the Zoho Books consent
 * (API-5, `project-docs/specs/api_connections.md` §2.11).
 *
 * The gateway route (`POST /integrations/api/zoho_books/start`, API-2) needs
 * `integrations:manage`, revokes the admin's own older pending pick, signs a
 * marked state and answers `{url}`, the Zoho consent URL. For an org that
 * `API_CONNECTIONS_ORGS` does not hold it answers 404, and so does this route.
 *
 * The rules, each with a case in `../route.test.ts`:
 *
 * 1. **No body goes upstream.** The org and the member come from the session
 *    at the gateway (R5).
 * 2. **Same origin only.** A start that another site sends gets 403.
 * 3. **The URL comes from the gateway, whole.** This route passes it on only
 *    when it is an absolute `https` URL with no user and no password, on a
 *    Zoho accounts host (`lib/zohoAccountsHosts.ts`). Any other answer is a
 *    502. So the route never becomes an open redirect.
 * 4. **A refusal carries a short code.** A 404 with no code reads
 *    `not_available`. A gateway 3xx is a 502.
 * 5. **Nothing logs the answer.** The consent URL holds the state.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { ZOHO_ACCOUNTS_HOSTS } from "@/lib/zohoAccountsHosts";

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
  // P3 of the review: only a Zoho accounts host, on the default port.
  if (url.port !== "" || !ZOHO_ACCOUNTS_HOSTS.includes(url.hostname)) return null;
  return url.toString();
}

function refusalCode(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && CODE.test(detail)) return detail;
  if (status === 404) return "not_available";
  return "failed";
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  if (!sameOrigin(req)) return refuse("cross_origin", 403);
  let res: Response;
  try {
    res = await gatewayFetch(`${GATEWAY_URL}/integrations/api/zoho_books/start`, {
      method: "POST",
      headers: await gatewayHeaders(),
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(START_TIMEOUT_MS),
    });
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
