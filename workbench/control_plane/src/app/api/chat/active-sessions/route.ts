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

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  // Gate when auth is enabled (no-op in dev).  Returns an empty list rather
  // than 401 so the sidebar degrades gracefully for unauthenticated polls.
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/chat/active-sessions`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) {
      return NextResponse.json([], { status: 200 }); // degrade gracefully
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json([], { status: 200 }); // degrade gracefully
  }
}
