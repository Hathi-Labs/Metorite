/**
 * GET /api/me/whatsapp-link → gateway `GET /me/whatsapp-link` (WS-47 WAC-1).
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §5.2.
 *
 * The channel state and the signed-in member's own links. The browser cannot
 * call the gateway itself: it carries no bearer and no `X-User-Email`, so
 * this hop adds them through `proxyToGateway`. It forwards no query and no
 * body, because the gateway takes the member and the organization from the
 * session and from nowhere else (R11).
 *
 * The gateway's answer passes through as it is. A 404 means the channel is
 * dark for this organization, and the section then draws nothing.
 */
import { NextResponse } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  try {
    return await proxyToGateway("/me/whatsapp-link", { method: "GET" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
