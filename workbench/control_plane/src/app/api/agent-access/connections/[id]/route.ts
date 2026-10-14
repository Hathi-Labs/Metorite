/**
 * DELETE /api/agent-access/connections/[id] — revoke one of the member's own
 * agent connections (WS-55 EA-4b). Same origin only. The rules are in
 * `../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayRevoke } from "../relay";

export const dynamic = "force-dynamic";

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  return relayRevoke(req, "/agent-access/connections", id);
}
