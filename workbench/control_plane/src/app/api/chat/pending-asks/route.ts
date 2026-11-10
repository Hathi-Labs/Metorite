/**
 * GET /api/chat/pending-asks?threadId=<id>
 *
 * The questions of one chat that still wait for an answer (WS-51 S2,
 * `chat_run_continuity.md` §4 S2). The chat draws each card again from this,
 * after its run parked, a reload or a restart (`lib/pendingAsks.ts`).
 *
 * The gateway reads the rows under the signed-in member's tenant, and only
 * for a member who may send in the room. Any failure is `[]`: the chat then
 * shows what it showed before.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const threadId = req.nextUrl.searchParams.get("threadId") ?? "";
  if (!threadId) return NextResponse.json([], { status: 200 });
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/pending-asks?thread_id=${encodeURIComponent(threadId)}`,
      { headers: await gatewayHeaders(), signal: AbortSignal.timeout(5_000) },
    );
    if (!res.ok) return NextResponse.json([], { status: 200 });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}
