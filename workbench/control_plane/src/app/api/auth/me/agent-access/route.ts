/**
 * GET and PUT /api/auth/me/agent-access — the member's agent switches (WS-55 EA-6h).
 *
 * Proxies the gateway's `/auth/me/agent-access` (`external_agent_access.md`
 * EA3-51). Today it holds one switch, "Let my AI agents delete tasks", which
 * the AI agents view of /integrations draws.
 *
 * ⚠️ A failure is an error, never "off". The card must not show a switch the
 * gateway did not answer for. So an unreachable gateway answers 502, and a
 * refusal keeps its status. A 404 means the agent flag is off.
 *
 * The identity is the session's. The body names no member and no tenant
 * (R11), and the gateway refuses any key but `delete_enabled`.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The body is `{"delete_enabled": true}`. The cap stops anything larger. */
const MAX_BODY = 256;

async function relay(res: Response): Promise<NextResponse> {
  if (res.ok) return NextResponse.json(await res.json());
  return NextResponse.json({ error: "agent_access_unavailable" }, { status: res.status });
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/agent-access`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "agent_access_unavailable" }, { status: 502 });
  }
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await req.text();
  if (body.length > MAX_BODY) {
    return NextResponse.json({ error: "body_too_large" }, { status: 413 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/agent-access`, {
      method: "PUT",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: body || "null",
      cache: "no-store",
      signal: AbortSignal.timeout(6_000),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "agent_access_unavailable" }, { status: 502 });
  }
}
