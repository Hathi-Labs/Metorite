/**
 * POST /api/observability/avatars/generate
 *
 * Proxies POST /observability/avatars/generate — the gateway calls Pixel Lab
 * (key held server-side) and returns a transparent pixel-art sprite as a
 * data-URI. Generation can take tens of seconds, hence the long timeout.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";
export const maxDuration = 120;

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/observability/avatars/generate`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(115_000),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { error: "Pixel Lab request failed or timed out" },
      { status: 504 },
    );
  }
}
