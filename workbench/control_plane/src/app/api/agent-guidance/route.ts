/**
 * POST /api/agent-guidance → gateway `POST /agent-guidance` (WS-MCP SW-31b).
 *
 * Spec: `project-docs/specs/agent_coordination.md` SW-31.
 *
 * Saves a new version of one scope: `{project_id, body}`, with `project_id`
 * null for the whole organization. The gateway takes the member and the
 * tenant from the session, and it decides who may write (403), which project
 * the member can see (404), an archived project (409) and the length (422).
 * This hop forwards the two keys and keeps the status. While the org is not
 * on `AGENT_GUIDANCE_ORGS`, the gateway answers 404, and the screen shows
 * that the feature is off.
 */
import { NextResponse, type NextRequest } from "next/server";

import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  const raw = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  if (!raw || typeof raw.body !== "string") {
    return NextResponse.json({ detail: "Send the guidance text as body." }, { status: 422 });
  }
  // Absent or null is the whole organization. Any other value must be a
  // non-empty string. A bad value is a 422, never an org save.
  const given = raw.project_id;
  if (given !== undefined && given !== null && (typeof given !== "string" || !given.trim())) {
    return NextResponse.json({ detail: "project_id must be a project id or null." }, { status: 422 });
  }
  const projectId = typeof given === "string" ? given : null;
  try {
    return await proxyToGateway("/agent-guidance", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, body: raw.body }),
    });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
}
