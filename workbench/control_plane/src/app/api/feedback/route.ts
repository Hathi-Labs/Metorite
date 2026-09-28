/**
 * POST /api/feedback — record a 👍/👎 vote on an assistant message.
 *
 * Thin proxy to the gateway POST /chat/feedback, which persists the vote as an
 * audit event (acb_audit). Forwards the signed-in user's email so the audit
 * actor is the real user.
 */
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

// These handlers resolve the signed-in member, so they can never be
// statically evaluated. Without this, `next build`'s page-data collection
// runs them with no request and no session.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

interface FeedbackBody {
  message_id?: string;
  vote?: string;
  session_id?: string;
}

export async function POST(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let body: FeedbackBody;
  try {
    body = (await req.json()) as FeedbackBody;
  } catch {
    return new Response("Invalid JSON body", { status: 400 });
  }
  if (!body.message_id || (body.vote !== "up" && body.vote !== "down")) {
    return new Response("message_id and vote ('up'|'down') are required", { status: 400 });
  }

  const headers: Record<string, string> = await gatewayHeaders({ "Content-Type": "application/json" });
  try {
    const session = await auth();
    if (session?.user?.email) headers["X-User-Email"] = session.user.email;
  } catch {
    /* non-request context — fall back to internal-only */
  }

  try {
    const r = await gatewayFetch(`${GATEWAY_URL}/chat/feedback`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    return new Response(null, { status: r.ok ? 204 : 502 });
  } catch {
    return new Response(null, { status: 502 });
  }
}
