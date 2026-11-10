/**
 * DELETE /api/integrations/mcp/[name]
 * PATCH  /api/integrations/mcp/[name]   the two switches of an MCP server:
 *        "always confirm" (H-300) and the read-only key (MCP-C2)
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
 * The switches of the body, or the words of a 422. Either key or both. A key
 * that is not a JSON boolean is 422, and so is a body with neither key.
 */
function switchesOf(raw: unknown): Record<string, boolean> | string {
  const body = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const out: Record<string, boolean> = {};
  for (const key of SWITCH_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(body, key)) continue;
    const value = body[key];
    if (typeof value !== "boolean") return `${key} must be true or false.`;
    out[key] = value;
  }
  return Object.keys(out).length ? out : "Send always_confirm, read_only_key, or both.";
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
