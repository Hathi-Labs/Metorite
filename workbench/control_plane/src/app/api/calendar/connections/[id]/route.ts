/**
 * DELETE /api/calendar/connections/[id] -> gateway `DELETE /tasks/calendar/connections/{id}`
 * (WS-CAL CE-10c).
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
