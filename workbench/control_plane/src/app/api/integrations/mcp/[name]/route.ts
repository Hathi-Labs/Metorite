/**
 * DELETE /api/integrations/mcp/[name]
 * PATCH  /api/integrations/mcp/[name]   the "always confirm" switch (H-300)
 *
 * Proxies to the FastAPI gateway. The gateway checks `integrations:manage`
 * and takes the organization from the session, never from the body.
 */

import { NextRequest, NextResponse } from "next/server";
import { refuseBadSegment } from "@/app/api/integrations/safeSegment";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  const bad = refuseBadSegment(name);
  if (bad) return bad;
  const body = (await req.json().catch(() => ({}))) as { always_confirm?: unknown };
  if (typeof body.always_confirm !== "boolean") {
    return NextResponse.json({ detail: "always_confirm must be true or false." }, { status: 422 });
  }
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/mcp/${encodeURIComponent(name)}`,
      {
        method: "PATCH",
        headers: await gatewayHeaders({ "Content-Type": "application/json" }),
        // Only the flag goes on, so nothing else in the body reaches the row.
        body: JSON.stringify({ always_confirm: body.always_confirm }),
        signal: AbortSignal.timeout(10_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  const bad = refuseBadSegment(name);
  if (bad) return bad;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/mcp/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(10_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
