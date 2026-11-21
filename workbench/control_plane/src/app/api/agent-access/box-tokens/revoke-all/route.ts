/**
 * POST /api/agent-access/box-tokens/revoke-all — revoke every box token of
 * the organization, for an admin (WS-55 EA-19c). It stays live while the box
 * flag is off. The rules are in `../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayRevokeAll } from "../relay";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  return relayRevokeAll(req);
}
