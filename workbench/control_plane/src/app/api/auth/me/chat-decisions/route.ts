/**
 * GET /api/auth/me/chat-decisions?session_id=… — the decisions that a chat
 * keeps for the member (HRD-12b, `chat_failure_modes.md` §6). Proxies the
 * gateway's `/auth/me/chat-decisions`.
 *
 * The identity is the session's. The query names a chat and nothing else (R5).
 * The relay refuses a chat id that the gateway would refuse, so no odd text
 * reaches a URL. A gateway that cannot be reached answers 502, and the chip
 * draws nothing.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { SESSION_ID } from "@/lib/chatDecisions";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const sessionId = req.nextUrl.searchParams.get("session_id") ?? "";
  if (!SESSION_ID.test(sessionId)) {
    return NextResponse.json({ detail: "session_id is not a chat id" }, { status: 422 });
  }
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/auth/me/chat-decisions?session_id=${encodeURIComponent(sessionId)}`,
      {
        headers: await gatewayHeaders(),
        cache: "no-store",
        signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
      },
    );
    return new Response(await res.text(), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "chat_decisions_unavailable" }, { status: 502 });
  }
}
