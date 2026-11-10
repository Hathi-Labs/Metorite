/**
 * GET /api/brief/today?kind=brief|closeout → gateway `GET /brief/today`
 * (WS-BRIEF BR-2c-1).
 *
 * Spec: `project-docs/specs/core_brief.md` §5 item 3.
 *
 * The signed-in member's stored brief of today, with its claims. The gateway
 * takes the member, the tenant and the date from the session only (R11), so
 * this hop forwards `kind` and nothing else. With `BRIEF_IN_APP` off the
 * gateway answers 404, and this hop keeps the status.
 */
import { NextResponse, type NextRequest } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  const kind = req.nextUrl.searchParams.get("kind");
  const query = kind === null ? "" : `?${new URLSearchParams({ kind })}`;
  try {
    return await proxyToGateway(`/brief/today${query}`, { method: "GET" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
