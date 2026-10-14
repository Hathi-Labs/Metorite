/**
 * GET /api/agent-access/connections — the member's own live agent connections
 * (WS-55 EA-4b). The rules are in `./relay.ts`.
 */
import type { NextResponse } from "next/server";
import { relayList } from "./relay";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  return relayList("/agent-access/connections");
}
