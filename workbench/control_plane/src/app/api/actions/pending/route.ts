/**
 * GET /api/actions/pending
 *
 * Proxies GET /actions/pending from the FastAPI gateway — the Action Broker
 * approval queue (outward writes an agent proposed under ACTION_BROKER_ENFORCE).
 * Keeps the internal bearer server-side.
 *
 * A failure is an error, never an empty queue (WS-58 MD-3a). The page reads
 * an empty queue as "nothing waits", and a link to one proposal reads it as
 * "that request is no longer waiting". So a gateway that cannot be reached,
 * that times out, or that answers 5xx gives 502. A gateway 4xx keeps its
 * status. Fence: `route.test.ts`.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export interface PendingAction {
  id: string;
  actor: string;
  action: string;
  target: string;
  payload?: Record<string, unknown>;
  authority: string;
  destructive?: boolean;
  disposition: string;
  status: string;
  created_at?: string;
}

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/actions/pending`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(4_000),
    });
    if (res.ok) {
      const body = (await res.json()) as { pending?: PendingAction[] };
      return NextResponse.json(body.pending ?? []);
    }
    const status = res.status >= 400 && res.status < 500 ? res.status : 502;
    return NextResponse.json({ error: "Could not load the approval queue." }, { status });
  } catch {
    // The gateway is unreachable, or it timed out.
    return NextResponse.json({ error: "Could not load the approval queue." }, { status: 502 });
  }
}
