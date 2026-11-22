/**
 * GET /api/integrations/api/zoho_books — the status of the org's Zoho Books
 * connection (API-5, `project-docs/specs/api_connections.md` §2.11).
 *
 * The gateway route (`GET /integrations/api/zoho_books`, API-2) needs
 * `integrations:manage`. It answers 404 for an org that `API_CONNECTIONS_ORGS`
 * does not hold, and this route passes the 404 on. The console reads that 404
 * as "not open to this org", so no public flag reaches the browser.
 *
 * The rules, each with a case in `route.test.ts`:
 *
 * 1. Only `pick` goes upstream, and only in the shape of a pick handle. The
 *    org and the member come from the session at the gateway (R5).
 * 2. The answer holds no token: the gateway sends public facts only. This
 *    route passes the JSON on, with `no-store`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };
const PICK = /^[A-Za-z0-9_-]{16,64}$/;

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const pick = req.nextUrl.searchParams.get("pick");
  const qs = pick !== null && PICK.test(pick) ? `?pick=${encodeURIComponent(pick)}` : "";
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/integrations/api/zoho_books${qs}`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status, headers: NO_STORE });
  } catch {
    return NextResponse.json({ detail: "unreachable" }, { status: 502, headers: NO_STORE });
  }
}
