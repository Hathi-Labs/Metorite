/**
 * DELETE /api/agent-access/connections/[id] — revoke one of the member's own
 * agent connections (WS-55 EA-4b).
 * PATCH  /api/agent-access/connections/[id] — change its grant (EA-17c-1,
 * D-EA-13).
 *
 * Both are same origin only. The rules are in `../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayChange, relayRevoke } from "../relay";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  return relayRevoke(req, "/agent-access/connections", id);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  return relayChange(req, id);
}
