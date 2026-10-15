/**
 * GET /api/me/reach/preview → gateway `GET /reach/preview` (WS-59 RE-1a).
 *
 * Spec: `project-docs/specs/proactive_reach.md` §16.1.
 *
 * What Reach would send the signed-in member now: the brief, the batched
 * interrupt, and whether the WhatsApp window is open. It sends nothing. The
 * gateway takes the member from the session only (R11), so this hop forwards
 * no query and no body. A 404 means Reach is dark for this member, and the
 * section then draws nothing.
 */
import { NextResponse } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  try {
    return await proxyToGateway("/reach/preview", { method: "GET" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
