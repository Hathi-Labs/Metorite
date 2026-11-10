/**
 * GET /api/agent-access/requests — the app requests that wait for the member
 * (WS-MCP MCP-P1b-1). The rules are in `./relay.ts`.
 */
import type { NextResponse } from "next/server";
import { relayList } from "./relay";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  return relayList();
}
