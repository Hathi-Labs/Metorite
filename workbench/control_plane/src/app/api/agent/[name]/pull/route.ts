/**
 * POST /api/agent/[name]/pull
 *
 * Proxies POST /agent/{name}/pull to the FastAPI gateway.
 * Pulls latest commits from origin into the agent's local clone.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/agent/${encodeURIComponent(name)}/pull`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(15_000),
      },
    );
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
