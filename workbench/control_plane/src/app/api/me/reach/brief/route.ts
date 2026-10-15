/**
 * POST /api/me/reach/brief → gateway `POST /reach/brief` (WS-59 RE-1a).
 *
 * Spec: `project-docs/specs/proactive_reach.md` §16.1.
 *
 * Sends the signed-in member's own brief to their own WhatsApp now. The
 * gateway still checks the link, the allowlist and the window. It takes the
 * member from the session only (R11), so this hop forwards no body.
 */
import { NextResponse } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  try {
    return await proxyToGateway("/reach/brief", { method: "POST" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
