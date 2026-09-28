/**
 * GET /api/agent/config?repo=owner/repo
 *
 * Proxies to GET /agent/config?repo=... on the FastAPI gateway.
 * Returns the parsed config.json from the agent's GitHub repository.
 * Used by the Add Agent modal to auto-fill form fields.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const repo = req.nextUrl.searchParams.get("repo") ?? "";
  if (!repo.trim()) {
    return NextResponse.json({ error: "repo parameter is required" }, { status: 400 });
  }

  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/agent/config?repo=${encodeURIComponent(repo)}`,
      {
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(10_000),
      }
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
