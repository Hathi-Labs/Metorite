/**
 * GET /api/chat/active-sessions
 *
 * Returns the list of session IDs whose agents are currently executing
 * (queried from the gateway's Redis cc:active:* scan).
 *
 * Used by the conversations sidebar to show a pulsing green dot next
 * to sessions that are still running in the background, even after a
 * browser refresh.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { RUNS_PARTIAL_HEADER, activeSessionsPath } from "@/lib/activeSessionsPath";

export const dynamic = "force-dynamic";

/**
 * A degraded answer: an empty list that SAYS it is partial (WS-51 S5). The
 * old callers read the body alone, so they still see "nothing runs". The
 * one poller (`lib/liveRuns.ts`) reads the header and keeps its last list,
 * so a lost poll never reads as "every run ended". Not a 503: every own-API
 * 5xx raises "Metorite is updating" in the shell (`lib/shell/serviceHealth.ts`).
 */
function partialEmpty(): NextResponse {
  return NextResponse.json([], { status: 200, headers: { [RUNS_PARTIAL_HEADER]: "1" } });
}

export async function GET(req: Request): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}${activeSessionsPath(req.url)}`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) return partialEmpty();
    const data = await res.json();
    // The gateway marks its own degraded lists (a Redis, Postgres or question
    // read error). Pass the mark on.
    const partial = res.headers.get(RUNS_PARTIAL_HEADER);
    return NextResponse.json(data, partial ? { headers: { [RUNS_PARTIAL_HEADER]: "1" } } : undefined);
  } catch {
    return partialEmpty();
  }
}
