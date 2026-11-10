/**
 * GET /api/agent-access/requests/[id] — one app request of the member
 * (WS-MCP MCP-P1b-1). The rules are in `../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayOne } from "../relay";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  return relayOne(id);
}
