/**
 * GET /api/agent-kit — the index of the agent kit (KIT-4b, `orchestration_playbook.md` §6a).
 *
 * Proxies the gateway's `GET /agent-kit`: `{kit_version, clients}`. The gateway
 * answers 404 while the org is not on `playbook_orgs`, and the control then
 * draws nothing. Every other failure keeps its status, or becomes 502.
 * The identity is the session's. The request names no tenant (R5).
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/agent-kit`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    return NextResponse.json({ error: "agent_kit_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "agent_kit_unavailable" }, { status: 502 });
  }
}
