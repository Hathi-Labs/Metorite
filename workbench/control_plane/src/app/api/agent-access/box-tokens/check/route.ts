/**
 * POST /api/agent-access/box-tokens/check — check a mint or a rotation of a
 * box token, and get the one-time `box-mint` ticket (WS-55 EA-19c). The
 * rules are in `../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayBox } from "../relay";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  return relayBox(req, "check");
}
