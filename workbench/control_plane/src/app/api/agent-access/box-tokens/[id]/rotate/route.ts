/**
 * POST /api/agent-access/box-tokens/[id]/rotate — give one of the member's
 * own box connections a new token (WS-55 EA-19c). It spends the ticket of
 * `../../check`. The rules are in `../../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayBox } from "../../relay";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  return relayBox(req, "rotate", id);
}
