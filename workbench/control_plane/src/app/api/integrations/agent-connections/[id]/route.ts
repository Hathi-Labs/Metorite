/**
 * DELETE /api/integrations/agent-connections/[id] — revoke any member's agent
 * connection in the org (WS-55 EA-4b). Same origin only. The gateway needs
 * `feature:integrations` and `integrations:manage`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayRevoke } from "@/app/api/agent-access/connections/relay";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  return relayRevoke(req, "/integrations/agent-connections", id);
}
