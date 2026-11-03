/**
 * GET/POST/PUT/PATCH/DELETE /api/studio and /api/studio/[…path]
 *
 * Proxies the AI Studio requests to the FastAPI gateway /studio/* — the
 * browser talks to the Next.js server, which forwards authenticated requests
 * via the shared gateway helper (internal bearer + user identity).
 *
 * A copy of the workflows proxy for the prefix `/studio`, with PATCH added
 * for the skill state route (STU-F2b). It adds no header, no identity and no
 * path rule: the gateway holds the real checks (the member, the feature, the
 * STUDIO_ORGS switch and the org of the bound session).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  const base =
    path.length > 0
      ? `${GATEWAY_URL}/studio/${path.join("/")}`
      : `${GATEWAY_URL}/studio`;
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  req: NextRequest,
  params: Promise<{ path?: string[] }>
): Promise<NextResponse> {
  // Guarded here rather than in each verb: all five delegate to this, and
  // `gatewayHeaders` throwing inside the try below would surface as a 502,
  // which tells an unauthenticated caller the gateway is down rather than
  // that they are not signed in.
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  const { path = [] } = await params;
  const refused = refuseUnsafePath(path);
  if (refused) return refused;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const init: RequestInit = {
      method,
      headers: await gatewayHeaders(
        method === "GET" || method === "DELETE"
          ? {}
          : { "Content-Type": "application/json" }
      ),
      signal: AbortSignal.timeout(60_000),
    };
    if (method !== "GET" && method !== "DELETE") {
      const body = await req.json().catch(() => ({}));
      init.body = JSON.stringify(body);
    }
    // A refused connect and a stale keep-alive socket are retried inside
    // `gatewayFetch` (H-194). Do not add a second retry here: it would
    // double the wait and could replay a write.
    const res = await gatewayFetch(upstream, init);
    if (res.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    const resBody = await res.json().catch(() => ({}));
    return NextResponse.json(resBody, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
): Promise<NextResponse> {
  return forward("GET", req, ctx.params);
}

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
): Promise<NextResponse> {
  return forward("POST", req, ctx.params);
}

export async function PUT(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
): Promise<NextResponse> {
  return forward("PUT", req, ctx.params);
}

export async function PATCH(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
): Promise<NextResponse> {
  return forward("PATCH", req, ctx.params);
}

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
): Promise<NextResponse> {
  return forward("DELETE", req, ctx.params);
}
