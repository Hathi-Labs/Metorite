/**
 * POST /api/agent-access/requests/[id]/decision — the member approves or
 * declines one app request (WS-MCP MCP-P1b-1). Same origin only. The rules are
 * in `../../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayDecision } from "../../relay";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  return relayDecision(req, id);
}
