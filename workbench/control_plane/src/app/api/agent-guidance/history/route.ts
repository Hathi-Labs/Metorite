/**
 * GET /api/agent-guidance/history?project_id=&limit= → gateway
 * `GET /agent-guidance/history` (WS-MCP SW-31b).
 *
 * Spec: `project-docs/specs/agent_coordination.md` SW-31.
 *
 * The versions of one scope, newest first. No `project_id` is the whole
 * organization. This hop forwards the two keys and nothing else, and it
 * keeps the status. A 404 with the detail "Not Found" means the org is not
 * on `AGENT_GUIDANCE_ORGS`.
 */
import { NextResponse, type NextRequest } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  const params = new URLSearchParams();
  const projectId = req.nextUrl.searchParams.get("project_id");
  const limit = req.nextUrl.searchParams.get("limit");
  if (projectId) params.set("project_id", projectId);
  if (limit) params.set("limit", limit);
  const query = params.size ? `?${params}` : "";
  try {
    return await proxyToGateway(`/agent-guidance/history${query}`, { method: "GET" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
