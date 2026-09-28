/**
 * POST /api/agent/mutations/[id]/remutate
 *
 * Proxies POST /agent/mutations/pending/{id}/remutate to the gateway.
 * Resets an eval_failed commit from the local clone so the operator can
 * trigger a fresh mutation attempt from chat.
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
      `${GATEWAY_URL}/agent/mutations/pending/${encodeURIComponent(id)}/remutate`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(10_000),
      }
    );
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: String(err) },
      { status: 502 }
    );
  }
}
