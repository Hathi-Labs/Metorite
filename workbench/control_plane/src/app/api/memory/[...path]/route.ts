/**
 * GET  /api/memory/[userId]/status  — memory system status for a user
 * GET  /api/memory/[userId]         — list all memories for a user
 * POST /api/memory/[userId]/search  — semantic search
 * POST /api/memory/[userId]/add     — save a conversation
 * DELETE /api/memory/[userId]/[memoryId] — delete a memory
 *
 * All routes proxy to the FastAPI gateway /memory/* path.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/*
 * A request with no session used to forward the internal token and NO
 * identity, and the gateway reads a bearer-without-headers call as the
 * platform acting as itself — full service access, scope check bypassed
 * (acb_auth/deps.py §1b). `/api/memory/` is in the proxy's public list, so
 * this route was an unauthenticated read of any scope named in the URL.
 *
 * The local session check that first closed that is gone: `requireIdentity`
 * is the same check, and `gatewayHeaders` can no longer produce the
 * identity-free headers that made the omission dangerous in the first place.
 */

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  const upstream = `${GATEWAY_URL}/memory/${path.join("/")}`;
  try {
    const res = await gatewayFetch(upstream, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  const upstream = `${GATEWAY_URL}/memory/${path.join("/")}`;
  try {
    const body = await req.json().catch(() => ({}));
    const res = await gatewayFetch(upstream, {
      method: "POST",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5_000),
    });
    const resBody = await res.json().catch(() => ({}));
    return NextResponse.json(resBody, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  const upstream = `${GATEWAY_URL}/memory/${path.join("/")}`;
  try {
    const res = await gatewayFetch(upstream, {
      method: "DELETE",
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    return new NextResponse(null, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
