/**
 * GET/POST/PUT/PATCH/DELETE /api/knowledge/[…path]
 *
 * Proxies every Knowledge request to the FastAPI gateway's /knowledge/* API
 * (WS-61 KB-2a, `knowledge_base.md` §5.3). The browser talks to the Next
 * server, which holds the session and forwards an authenticated request
 * (internal bearer + X-User-Email) upstream. It is the shape of the Projects
 * proxy at /api/projects/[...path], with no If-Match and no multipart arm:
 * no Knowledge route takes either today. A file upload arrives with KB-2c.
 *
 * ⚠️ This is not a convenience layer. `/knowledge` is gated by
 * `require_knowledge_enabled` and `require_feature_router("knowledge")`, and
 * the gateway takes the acting identity from `X-User-Email` only. A page that
 * fetched the gateway directly would carry neither (workbench/AGENTS.md,
 * "Identity"). Nothing in this app may point the browser at api.* .
 *
 * The status passes through unchanged. The page reads it: a 404 on
 * `GET /knowledge/spaces` is the off state, and a 403 is "no feature"
 * (`app/knowledge/lib/phase.ts`).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  const base = `${GATEWAY_URL}/knowledge/${path.join("/")}`;
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  req: NextRequest,
  params: Promise<{ path: string[] }>
): Promise<NextResponse> {
  const { path } = await params;
  const refused = refuseUnsafePath(path);
  if (refused) return refused;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const init: RequestInit = {
      method,
      headers: {
        ...(await gatewayHeaders()),
        ...(method === "GET" || method === "DELETE"
          ? {}
          : { "Content-Type": "application/json" }),
      },
      signal: AbortSignal.timeout(30_000),
    };
    if (method !== "GET" && method !== "DELETE") {
      const body = await req.json().catch(() => ({}));
      init.body = JSON.stringify(body);
    }
    // A refused connect and a stale pooled socket are both retried inside
    // `gatewayFetch` (H-194). Do not add a second retry here: it could replay
    // a write.
    const res = await gatewayFetch(upstream, init);
    // Bytes, not a string: `Response.text()` is a UTF-8 decode, and a decode
    // strips a leading byte order mark (the BOM trap of the Projects proxy).
    const buf = await res.arrayBuffer();
    if (buf.byteLength === 0) return new NextResponse(null, { status: res.status });
    // The response type is the gateway's, not a constant.
    const headers: Record<string, string> = {
      "Content-Type": res.headers.get("content-type") ?? "application/json",
    };
    const noSniff = res.headers.get("x-content-type-options");
    if (noSniff) headers["X-Content-Type-Options"] = noSniff;
    return new NextResponse(buf, { status: res.status, headers });
  } catch (err) {
    return NextResponse.json(
      { detail: `Knowledge gateway unreachable: ${String(err)}` },
      { status: 502 }
    );
  }
}

// Every verb resolves the identity BEFORE forwarding, so a signed-out member
// gets 401 and not a 502 (`src/lib/gateway.test.ts`).

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("GET", req, ctx.params);
}
export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("POST", req, ctx.params);
}
export async function PUT(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("PUT", req, ctx.params);
}
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("PATCH", req, ctx.params);
}
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("DELETE", req, ctx.params);
}
