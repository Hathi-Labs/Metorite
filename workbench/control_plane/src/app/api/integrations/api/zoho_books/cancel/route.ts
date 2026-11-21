/**
 * POST /api/integrations/api/zoho_books/cancel — end the admin's own pending
 * pick of a Zoho Books consent (API-5 review, P2,
 * `project-docs/specs/api_connections.md` §2.11, gap 1).
 *
 * The pick sheet calls it when the admin closes the sheet, and the tab sends
 * it as a beacon when it closes. The gateway route
 * (`POST /integrations/api/zoho_books/cancel`) needs `integrations:manage`,
 * checks the member of the entry first, takes the entry, revokes its token
 * once and drops it.
 *
 * The rules, each with a case in `../route.test.ts`:
 *
 * 1. **Same origin only.** A beacon carries `sec-fetch-site: same-origin`. A
 *    cancel that another site sends gets 403.
 * 2. **One key goes upstream, in its shape.** `pick` is a pick handle. A bad
 *    shape is 400 `invalid_pick` here, and the gateway gets no call. The org
 *    and the member come from the session at the gateway (R5).
 * 3. **A refusal carries a short code.** A 404 with no code reads
 *    `not_available`.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const CANCEL_TIMEOUT_MS = 15_000;
const NO_STORE = { "Cache-Control": "no-store" };
const CODE = /^[a-z0-9_]{1,40}$/;
const PICK = /^[A-Za-z0-9_-]{16,64}$/;
const MAX_BODY_CHARS = 512;

function refuse(detail: string, status: number): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
}

function refusalCode(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && CODE.test(detail)) return detail;
  if (status === 404) return "not_available";
  return "failed";
}

/** The pick handle of the body, or null. A beacon may send any content type. */
async function pickOf(req: NextRequest): Promise<string | null> {
  const raw = await req.text().catch(() => "");
  if (!raw || raw.length > MAX_BODY_CHARS) return null;
  try {
    const pick = (JSON.parse(raw) as { pick?: unknown } | null)?.pick;
    return typeof pick === "string" && PICK.test(pick) ? pick : null;
  } catch {
    return null;
  }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  if (!sameOrigin(req)) return refuse("cross_origin", 403);
  const pick = await pickOf(req);
  if (pick === null) return refuse("invalid_pick", 400);
  let res: Response;
  try {
    res = await gatewayFetch(`${GATEWAY_URL}/integrations/api/zoho_books/cancel`, {
      method: "POST",
      headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify({ pick }),
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(CANCEL_TIMEOUT_MS),
    });
  } catch {
    return refuse("unreachable", 502);
  }
  if (res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400)) {
    return refuse("failed", 502);
  }
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) return refuse(refusalCode(body, res.status), res.status);
  const cancelled = (body as { cancelled?: unknown } | null)?.cancelled === true;
  return NextResponse.json({ cancelled }, { headers: NO_STORE });
}
