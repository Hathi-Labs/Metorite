/**
 * GET  /api/settings/llm/enabled-models   — list enabled model entries
 * POST /api/settings/llm/enabled-models   — enable a model
 */
import { NextRequest, NextResponse } from "next/server";
import { gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

// These handlers resolve the signed-in member, so they can never be
// statically evaluated. Without this, `next build`'s page-data collection
// runs them with no request and no session.
export const dynamic = "force-dynamic";

const GATEWAY = process.env.GATEWAY_BASE_URL ?? "http://localhost:8000";


export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  // Built per request, never hoisted to module scope: these headers name
  // the acting member, so a module constant would bind whoever imported
  // first and serve every later caller as them.
  const HEADERS = await gatewayHeaders({ "Content-Type": "application/json" });
  try {
    const r = await gatewayFetch(`${GATEWAY}/settings/llm/enabled-models`, {
      headers: HEADERS,
      signal: AbortSignal.timeout(4_000),
    });
    const data = await r.json().catch(() => []);
    return NextResponse.json(data, { status: r.status });
  } catch (_e) {
    return NextResponse.json([], { status: 200 }); // empty list on gateway down
  }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  // Built per request, never hoisted to module scope: these headers name
  // the acting member, so a module constant would bind whoever imported
  // first and serve every later caller as them.
  const HEADERS = await gatewayHeaders({ "Content-Type": "application/json" });
  const body = await req.json();
  try {
    const r = await gatewayFetch(`${GATEWAY}/settings/llm/enabled-models`, {
      method: "POST",
      headers: HEADERS,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(6_000),
    });
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  } catch (err) {
    return NextResponse.json({ detail: String(err) }, { status: 502 });
  }
}
