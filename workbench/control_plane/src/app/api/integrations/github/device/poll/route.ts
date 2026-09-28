/**
 * POST /api/integrations/github/device/poll
 *
 * Proxies to POST /integrations/github/device/poll on the gateway.
 * Polls GitHub to check if the user has approved the device flow.
 *
 * Request body: { device_code: string }
 * Response:
 *   { status: "authorized", login: string }
 *   { status: "pending" }
 *   { status: "slow_down", interval: number }
 *   { status: "expired" }
 *   { status: "denied" }
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let body: unknown;
  try {
    body = await req.json();
  } catch (_e) {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/integrations/github/device/poll`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(20_000),
    });
    if (res.ok) {
      return NextResponse.json(await res.json());
    }
    const text = await res.text().catch(() => "");
    return NextResponse.json(
      { error: `Gateway ${res.status}: ${text}` },
      { status: res.status }
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
