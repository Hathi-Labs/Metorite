/**
 * GET /api/chat/pending-asks?threadId=<id>
 *
 * The questions of one chat that still wait for an answer (WS-51 S2,
 * `chat_run_continuity.md` §4 S2). The chat draws each card again from this,
 * after its run parked, a reload or a restart (`lib/pendingAsks.ts`).
 *
 * The gateway reads the rows under the signed-in member's tenant, and only
 * for a member who may send in the room.
 *
 * A failure is a failure (RESUME-0a, `chat_run_continuity.md` §4.12.2). An
 * empty list means "no card waits", and the chat settles its cards on it. So
 * a gateway error passes its status on, a timeout is 504 and a thrown fetch
 * is 502. The chat reads each of them as "unknown" and keeps its cards.
 * A missing `threadId` is 400: the caller made an error, and no thread has
 * an empty answer to give.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const threadId = req.nextUrl.searchParams.get("threadId") ?? "";
  if (!threadId) return NextResponse.json({ error: "threadId is required" }, { status: 400 });
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/pending-asks?thread_id=${encodeURIComponent(threadId)}`,
      { headers: await gatewayHeaders(), signal: AbortSignal.timeout(5_000) },
    );
    if (!res.ok) {
      return NextResponse.json({ error: "pending asks unavailable" }, { status: res.status });
    }
    return NextResponse.json(await res.json());
  } catch (e) {
    const timedOut = e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError");
    return NextResponse.json(
      { error: timedOut ? "pending asks timed out" : "pending asks unreachable" },
      { status: timedOut ? 504 : 502 },
    );
  }
}
