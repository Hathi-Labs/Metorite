/**
 * DELETE /api/calendar/connections/[id] -> gateway `DELETE /tasks/calendar/connections/{id}`
 * (WS-CAL CE-10c).
 *
 * PATCH /api/calendar/connections/[id] -> gateway `PATCH /tasks/calendar/connections/{id}`
 * (CE-10c follow-up): the switch "Show my Metorite tasks". The body is exactly `{push_tasks: boolean}`.
 * The route refuses any other key, any other type and a body over 256 bytes with a 400.
 *
 * Disconnect one calendar. The id must be a UUID before it enters the gateway path. The route
 * refuses a request from another site (`sameOrigin`). The status passes through, so a 404 stays 404.
 */
import { NextRequest, NextResponse } from "next/server";

import { isConnectionId } from "@/app/calendar/lib/calendarIds";
import { sameOrigin } from "@/lib/accountsServer";
import { proxyToGateway } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  if (!isConnectionId(id)) return NextResponse.json({ detail: "Invalid connection id." }, { status: 400 });
  if (!sameOrigin(req)) return NextResponse.json({ detail: "cross_origin" }, { status: 403 });
  try {
    return await proxyToGateway(`/tasks/calendar/connections/${id}`, { method: "DELETE" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}

const MAX_BODY_BYTES = 256;

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  if (!isConnectionId(id)) return NextResponse.json({ detail: "Invalid connection id." }, { status: 400 });
  if (!sameOrigin(req)) return NextResponse.json({ detail: "cross_origin" }, { status: 403 });
  const text = await req.text().catch(() => "");
  if (text.length > MAX_BODY_BYTES) return NextResponse.json({ detail: "Body too large." }, { status: 400 });
  let body: unknown = null;
  try {
    body = JSON.parse(text);
  } catch {
    /* falls to the refusal below */
  }
  const keys = body && typeof body === "object" && !Array.isArray(body) ? Object.keys(body) : [];
  if (keys.length !== 1 || keys[0] !== "push_tasks" || typeof (body as Record<string, unknown>).push_tasks !== "boolean") {
    return NextResponse.json({ detail: "Send exactly {push_tasks: true or false}." }, { status: 400 });
  }
  try {
    return await proxyToGateway(`/tasks/calendar/connections/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ push_tasks: (body as { push_tasks: boolean }).push_tasks }),
    });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
