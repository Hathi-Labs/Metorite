/**
 * GET / PUT /api/agent/[name]/skills
 *
 * Proxies to GET/PUT /agent/{name}/skills on the FastAPI gateway (WS-23 S2).
 * GET returns the stored per-agent skill-family settings; PUT replaces them
 * wholesale (gateway-gated admin:access:manage — a skill toggle is an access
 * decision). Family metadata + measured token costs come from
 * /api/integrations/skills (the S1 catalog); this route is settings only.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export interface SkillSetting {
  family: string;
  enabled: boolean;
  reason: string;
  set_by: string;
  set_at: string;
}

export interface AgentSkillSettings {
  agent: string;
  instance: string;
  settings: SkillSetting[];
  /** Families explicitly toggled off — the enforcement seam reads these. */
  disabled_families: string[];
  /** family slug → why it cannot be toggled (core floor / App grants). */
  non_toggleable: Record<string, string>;
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/agent/${encodeURIComponent(name)}/skills`,
      {
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(15_000),
      },
    );
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  try {
    const payload = await req.json().catch(() => ({}));
    const res = await gatewayFetch(
      `${GATEWAY_URL}/agent/${encodeURIComponent(name)}/skills`,
      {
        method: "PUT",
        headers: {
          ...(await gatewayHeaders()),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(15_000),
      },
    );
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
