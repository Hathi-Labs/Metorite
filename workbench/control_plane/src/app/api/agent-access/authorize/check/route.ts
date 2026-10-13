/**
 * POST /api/agent-access/authorize/check — validate an outside agent's
 * authorize request, and get the one-time consent ticket (WS-55 EA-4a).
 *
 * The rules are in `../relay.ts`: same origin only, and never a 3xx.
 */
import type { NextRequest, NextResponse } from "next/server";
import { relayConsent } from "../relay";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  return relayConsent(req, "check");
}
