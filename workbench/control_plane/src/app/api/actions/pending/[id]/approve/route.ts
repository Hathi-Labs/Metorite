/**
 * POST /api/actions/pending/[id]/approve
 *
 * Proxies POST /actions/pending/{id}/approve to the gateway — approving runs
 * the broker's registered handler (executes the real outward write). Keeps the
 * internal bearer server-side.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { id } = await params;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/actions/pending/${encodeURIComponent(id)}/approve`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(15_000),
      }
    );
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
