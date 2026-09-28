/**
 * DELETE /api/agent/[name]
 * PATCH  /api/agent/[name]
 *
 * Proxies to the corresponding FastAPI gateway endpoints.
 * Name validation is handled by the backend — the proxy just
 * encodes and forwards.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** Reject names that are empty or contain path separators (safety only). */
function validateName(name: string): boolean {
  if (!name || name.trim().length === 0) return false;
  // Reject path traversal / separators
  if (name.includes("/") || name.includes("\\") || name.includes("..")) return false;
  return true;
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ name: string }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;

  if (!validateName(name)) {
    return NextResponse.json({ error: "Invalid agent name" }, { status: 400 });
  }

  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/agent/${encodeURIComponent(name)}`, {
      method: "DELETE",
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(8_000),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await params;

  if (!validateName(name)) {
    return NextResponse.json({ error: "Invalid agent name" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch (_e) {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/agent/${encodeURIComponent(name)}`, {
      method: "PATCH",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(8_000),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
