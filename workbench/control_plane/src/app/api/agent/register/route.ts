/**
 * POST /api/agent/register
 *
 * Proxies to POST /agent on the FastAPI gateway to register a new agent
 * from a GitHub repository.
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
    const res = await gatewayFetch(`${GATEWAY_URL}/agent`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
