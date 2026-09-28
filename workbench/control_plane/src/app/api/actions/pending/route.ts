/**
 * GET /api/actions/pending
 *
 * Proxies GET /actions/pending from the FastAPI gateway — the Action Broker
 * approval queue (outward writes an agent proposed under ACTION_BROKER_ENFORCE).
 * Keeps the internal bearer server-side. Returns an empty queue when the
 * gateway is unavailable so the inbox UI never breaks.
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
  } catch {
    // Gateway unavailable — return an empty queue.
  }
  return NextResponse.json([]);
}
