/**
 * GET /api/agent/mutations/[id]/diff
 *
 * Proxies GET /agent/mutations/pending/{id}/diff from the gateway.
 * Returns the unified diff text for inline review in the inbox.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/agent/mutations/pending/${encodeURIComponent(id)}/diff`,
      {
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(8_000),
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
