/**
 * GET /api/shell/feed?limit= — My Day as a live feed (`my_day_feed.md`
 * §14.1a, WS-57 MD-1b).
 *
 * Proxies the gateway's `GET /shell/feed`. The answer holds the ranked cards,
 * their groups, and the needs view of the same provider run, so My Day reads
 * one answer and the bell's counts stay true (`src/lib/shell/feed.ts`).
 *
 * The same shape as `needs/route.ts`. An empty list would say "You are caught
 * up", which is a claim, not a silence. So a failure answers 502, and the
 * column says it could not load and offers Retry.
 *
 * The identity is the session's. The request carries no tenant and no
 * member (R11), and `limit` is the only value passed on, capped.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The gateway's own ceiling and default (§14.1a: 40 by default, 80 at most). */
const MAX_LIMIT = 80;
const DEFAULT_LIMIT = 40;

/**
 * The needs providers keep their 4 s budget, and the feed-only providers
 * (Calendar) run after them. 8 s covers both, plus the trip.
 */
const TIMEOUT_MS = 8_000;

// Not exported: a route file may export only its handlers and route config.
function feedLimit(raw: string | null): number {
  const n = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(n) || n < 1) return DEFAULT_LIMIT;
  return Math.min(n, MAX_LIMIT);
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const params = new URLSearchParams({
    limit: String(feedLimit(req.nextUrl.searchParams.get("limit"))),
  });
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/feed?${params}`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(TIMEOUT_MS)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    // A refusal keeps its status, so the column can tell "not yours" from
    // "not reachable".
    return NextResponse.json({ error: "feed_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "feed_unavailable" }, { status: 502 });
  }
}
