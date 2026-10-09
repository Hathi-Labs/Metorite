/**
 * POST /api/me/whatsapp-link/code → gateway `POST /me/whatsapp-link/code`
 * (WS-47 WAC-1).
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §5.2.
 *
 * Issues the signed-in member a single-use link code and its `wa.me` link.
 * ⚠️ It sends NO body upstream. The browser's body is not read at all, so a
 * page field can never name an organization or a member (R11). The gateway
 * takes both from the session.
 *
 * The answer holds the plain code once. Nothing here logs or caches it.
 */
import { NextResponse } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  try {
    const res = await proxyToGateway("/me/whatsapp-link/code", { method: "POST" });
    // The answer carries the plain code. proxyToGateway copies only the
    // content type, so set no-store here as well as in the gateway.
    res.headers.set("Cache-Control", "no-store");
    return res;
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
