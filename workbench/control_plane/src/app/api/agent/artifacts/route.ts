/**
 * GET /api/agent/artifacts?agent=&category=
 * Proxy to gateway /agent/artifacts — global artifact browser.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const upstream = new URL(`${GATEWAY_URL}/agent/artifacts`);
    const agent = req.nextUrl.searchParams.get("agent");
    const category = req.nextUrl.searchParams.get("category");
    if (agent) upstream.searchParams.set("agent", agent);
    if (category) upstream.searchParams.set("category", category);

    const res = await gatewayFetch(upstream.toString(), {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(15_000),
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
