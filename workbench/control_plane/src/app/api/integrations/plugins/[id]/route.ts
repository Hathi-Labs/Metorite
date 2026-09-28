/**
 * DELETE /api/integrations/plugins/[id]
 *
 * Proxies to DELETE /integrations/plugins/{id} on the FastAPI gateway.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { id } = await params;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/plugins/${encodeURIComponent(id)}`,
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
