/**
 * GET /api/me/reach/status → gateway `GET /reach/status` (WS-59 RE-5).
 *
 * Spec: `project-docs/specs/proactive_reach.md` §11.
 *
 * `{available, linked}` of "Tell me on WhatsApp" for the signed-in member.
 * The Rules UI reads it to offer the action, or to show it disabled until
 * the member links WhatsApp. The gateway takes the member from the session
 * only (R11), so this hop forwards no query and no body. The gateway answers
 * 200 when Reach is off, with `available: false`.
 */
import { NextResponse } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  try {
    return await proxyToGateway("/reach/status", { method: "GET" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
