/**
 * POST /api/integrations/api/zoho_books/pick — finish a Zoho Books consent
 * that found several organizations, with one of them (API-5,
 * `project-docs/specs/api_connections.md` §2.11).
 *
 * The gateway route (`POST /integrations/api/zoho_books/pick`, API-2) needs
 * `integrations:manage`. It checks the member of the pending entry before it
 * takes the entry, runs the test read, then saves. It answers the status.
 *
 * The rules, each with a case in `../route.test.ts`:
 *
 * 1. **Same origin only.** A pick that another site sends gets 403.
 * 2. **Two keys go upstream, in their shapes.** `pick` is a pick handle and
 *    `zoho_organization_id` is digits. Any other key is dropped. A bad shape
 *    is 400 `invalid_pick` here, and the gateway gets no call.
 * 3. **A refusal carries a short code.** A 404 with no code reads
 *    `not_available`.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const PICK_TIMEOUT_MS = 30_000;
const NO_STORE = { "Cache-Control": "no-store" };
const CODE = /^[a-z0-9_]{1,40}$/;
const PICK = /^[A-Za-z0-9_-]{16,64}$/;
const ORG_ID = /^[0-9]{1,20}$/;

function refuse(detail: string, status: number): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
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
  const sent = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const pick = sent?.pick;
  const id = sent?.zoho_organization_id;
  if (typeof pick !== "string" || !PICK.test(pick) || typeof id !== "string" || !ORG_ID.test(id)) {
    return refuse("invalid_pick", 400);
  }
  let res: Response;
  try {
    res = await gatewayFetch(`${GATEWAY_URL}/integrations/api/zoho_books/pick`, {
      method: "POST",
      headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify({ pick, zoho_organization_id: id }),
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(PICK_TIMEOUT_MS),
    });
  } catch {
    return refuse("unreachable", 502);
  }
  if (res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400)) {
    return refuse("failed", 502);
  }
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) return refuse(refusalCode(body, res.status), res.status);
  return NextResponse.json(body ?? {}, { headers: NO_STORE });
}
