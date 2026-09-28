/**
 * DELETE /api/settings/llm/enabled-models/[id] — disable a model
 */
import { NextRequest, NextResponse } from "next/server";
import { gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

// These handlers resolve the signed-in member, so they can never be
// statically evaluated. Without this, `next build`'s page-data collection
// runs them with no request and no session.
export const dynamic = "force-dynamic";

const GATEWAY = process.env.GATEWAY_BASE_URL ?? "http://localhost:8000";

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { id } = await params;
  const modelId = decodeURIComponent(id);
  try {
    const r = await gatewayFetch(
      `${GATEWAY}/settings/llm/enabled-models/${encodeURIComponent(modelId)}`,
      {
        method: "DELETE",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(6_000),
      },
    );
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  } catch (err) {
    return NextResponse.json({ detail: String(err) }, { status: 502 });
  }
}
