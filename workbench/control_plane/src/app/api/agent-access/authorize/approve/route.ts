/**
 * POST /api/agent-access/authorize/approve — the member approves an outside
 * agent (WS-55 EA-4a). The body carries the consent ticket from `check`.
 *
 * The answer is JSON `redirect_to`, never a 3xx. The page navigates there
 * itself. The rules are in `../relay.ts`.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayConsent } from "../relay";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  return relayConsent(req, "approve");
}
