/**
 * GET /api/integrations/agent-connections — every live agent connection of
 * the org (WS-55 EA-4b). The gateway needs `feature:integrations` and
 * `integrations:manage`. The rules are in the agent-access relay.
 */
import type { NextResponse } from "next/server";
import { relayList } from "@/app/api/agent-access/connections/relay";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  return relayList("/integrations/agent-connections");
}
