/**
 * POST /api/agent-access/box-tokens — make a box token for the member of the
 * session (WS-55 EA-19c). It spends the ticket of `./check`, and answers the
 * raw token once. The rules are in `./relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayBox } from "./relay";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  return relayBox(req, "mint");
}
