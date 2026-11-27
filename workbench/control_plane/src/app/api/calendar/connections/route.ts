/**
 * GET /api/calendar/connections -> gateway `GET /tasks/calendar/connections` (WS-CAL CE-10c).
 *
 * The list of connected calendars, with the providers that can connect. The status passes through
 * unchanged. A 404 means the gateway flag is off, and the page then draws no Connect UI.
 * No query and no body go upstream.
 */
import { NextResponse } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  try {
    return await proxyToGateway("/tasks/calendar/connections", { method: "GET" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
