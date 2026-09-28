/**
 * GET/POST/PUT/DELETE /api/workflows and /api/workflows/[…path]
 *
 * Proxies all Workflows app requests to the FastAPI gateway /workflows/* —
 * the browser talks to the Next.js server, which forwards authenticated
 * requests via the shared gateway helper (internal bearer + user identity).
 * The run SSE stream has its own dedicated route (runs/[runId]/stream) since
 * this proxy buffers response bodies.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  const base =
    path.length > 0
      ? `${GATEWAY_URL}/workflows/${path.join("/")}`
      : `${GATEWAY_URL}/workflows`;
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  method: "GET" | "POST" | "PUT" | "DELETE",
  req: NextRequest,
  params: Promise<{ path?: string[] }>
): Promise<NextResponse> {
  // Guarded here rather than in each verb: all four delegate to this, and
  // `gatewayHeaders` throwing inside the try below would surface as a 502,
  // which tells an unauthenticated caller the gateway is down rather than
  // that they are not signed in.
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  const { path = [] } = await params;
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
    // A pooled keep-alive socket can be closed by the gateway just as we
    // reuse it (undici vs uvicorn) — GETs are idempotent, retry once.
    let res: Response;
    try {
      res = await gatewayFetch(upstream, init);
    } catch (err) {
      if (method !== "GET") throw err;
      res = await gatewayFetch(upstream, {
        ...init,
        signal: AbortSignal.timeout(60_000),
      });
    }
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

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
): Promise<NextResponse> {
  return forward("DELETE", req, ctx.params);
}
