/**
 * GET /api/shell/needs?limit= — what needs the member, from every app (NS-3).
 *
 * Proxies the gateway's `GET /shell/needs` (`navigation_shell.md` §7.2). My
 * Day's "Needs you" card reads it, and the shell's one bell will read it too.
 *
 * The same shape as `search/route.ts`, with one difference on purpose. The
 * command bar treats search as an accelerant, so a gateway that is down
 * answers an empty list there. Here an empty list would say "Nothing needs
 * you", which is a claim, not a silence. So a failure answers 502, and the
 * card says it could not load and offers Retry.
 *
 * The identity is the session's. The request carries no tenant and no
 * member (R11), and `limit` is the only value passed on, capped.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The gateway's own ceiling (`MAX_LIMIT` in `routes/shell/needs.py`). */
const MAX_LIMIT = 50;
const DEFAULT_LIMIT = 30;

// Not exported: a route file may export only its handlers and route config.
function needsLimit(raw: string | null): number {
  const n = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(n) || n < 1) return DEFAULT_LIMIT;
  return Math.min(n, MAX_LIMIT);
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const params = new URLSearchParams({
    limit: String(needsLimit(req.nextUrl.searchParams.get("limit"))),
  });
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/needs?${params}`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      // The gateway's whole budget is 4 s (TOTAL_BUDGET_S), plus the trip.
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    // A refusal keeps its status, so the card can tell "not yours" from
    // "not reachable".
    return NextResponse.json({ error: "needs_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "needs_unavailable" }, { status: 502 });
  }
}
