/**
 * DELETE /api/integrations/mcp/[name]
 * PATCH  /api/integrations/mcp/[name]   the two switches of an MCP server:
 *        "always confirm" (H-300) and the read-only key (MCP-C2), and its
 *        description (CSY-7)
 *
 * Proxies to the FastAPI gateway. The gateway checks `integrations:manage`
 * and takes the organization from the session, never from the body.
 */

import { NextRequest, NextResponse } from "next/server";
import { refuseBadSegment } from "@/app/api/integrations/safeSegment";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The keys that the PATCH passes on. Nothing else in the body reaches the row. */
const SWITCH_KEYS = ["always_confirm", "read_only_key"] as const;

/**
 * The fields of the body, or the words of a 422. Either switch, the
 * description (CSY-7), or any of them. A switch that is not a JSON boolean
 * is 422, and so is a description that is not a string. A body with no key
 * is 422. The gateway holds the length bound.
 */
function switchesOf(raw: unknown): Record<string, boolean | string> | string {
  const body = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const out: Record<string, boolean | string> = {};
  for (const key of SWITCH_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(body, key)) continue;
    const value = body[key];
    if (typeof value !== "boolean") return `${key} must be true or false.`;
    out[key] = value;
  }
  if (Object.prototype.hasOwnProperty.call(body, "description")) {
    if (typeof body.description !== "string") return "description must be text.";
    out.description = body.description;
  }
  return Object.keys(out).length ? out : "Send always_confirm, read_only_key, description, or any of them.";
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  const bad = refuseBadSegment(name);
  if (bad) return bad;
  const switches = switchesOf(await req.json().catch(() => null));
  if (typeof switches === "string") {
    return NextResponse.json({ detail: switches }, { status: 422 });
  }
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/mcp/${encodeURIComponent(name)}`,
      {
        method: "PATCH",
        headers: await gatewayHeaders({ "Content-Type": "application/json" }),
        // Only the keys that the body named go on.
        body: JSON.stringify(switches),
        signal: AbortSignal.timeout(10_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;
  const bad = refuseBadSegment(name);
  if (bad) return bad;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/mcp/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(10_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
