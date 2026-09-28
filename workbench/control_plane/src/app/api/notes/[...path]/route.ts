/**
 * GET/POST/PATCH/DELETE /api/notes/[…path]
 *
 * Proxies all Note Taker requests to the FastAPI gateway /notes/* path —
 * the browser talks to the Next.js server, which forwards authenticated
 * requests (internal bearer + X-User-Email) to the gateway. Mirrors the
 * tasks app's proxy at /api/tasks/[...path] (multipart passthrough for
 * recording uploads, binary passthrough for audio playback).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  const base = `${GATEWAY_URL}/notes/${path.join("/")}`;
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  req: NextRequest,
  params: Promise<{ path: string[] }>
): Promise<NextResponse> {
  const { path } = await params;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const reqType = req.headers.get("content-type") ?? "";
    const isMultipart = reqType.startsWith("multipart/form-data");
    // Live-recording chunks arrive as raw binary — pass them through byte-exact
    // like multipart, not JSON-parsed.
    const isBinary = reqType.startsWith("application/octet-stream");
    const rawBody = isMultipart || isBinary;
    // Forward the browser's Range/If-Range for media playback — iOS Safari
    // sends a `Range: bytes=0-1` probe and will NOT play <audio> unless it gets
    // a 206 back. Starlette's FileResponse honours Range; we just relay it.
    const rangeHeaders: Record<string, string> = {};
    const rangeHeader = req.headers.get("range");
    if (rangeHeader) rangeHeaders["Range"] = rangeHeader;
    const ifRange = req.headers.get("if-range");
    if (ifRange) rangeHeaders["If-Range"] = ifRange;
    const init: RequestInit = {
      method,
      headers: {
        ...(await gatewayHeaders()),
        ...rangeHeaders,
        ...(method === "GET" || method === "DELETE"
          ? {}
          : // Multipart / binary must pass through byte-exact (with the
            // original content-type + boundary); everything else is JSON.
            { "Content-Type": rawBody ? reqType : "application/json" }),
      },
      // Recording uploads + long transcriptions outlive the tasks proxy's
      // 30s ceiling; audio files are large.
      signal: AbortSignal.timeout(120_000),
    };
    if (method !== "GET" && method !== "DELETE") {
      if (rawBody) {
        init.body = Buffer.from(await req.arrayBuffer());
      } else {
        const body = await req.json().catch(() => ({}));
        init.body = JSON.stringify(body);
      }
    }
    // A refused connect (a gateway restart) and a pooled keep-alive socket
    // that the gateway closed as we reused it are both retried inside
    // `gatewayFetch`, by the rules in `lib/gatewayFetch.ts` (H-194). Do not
    // add a second retry here: it would double the wait and could replay a write.
    const res = await gatewayFetch(upstream, init);
    if (res.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    const resType = res.headers.get("content-type") ?? "";
    if (!resType.includes("application/json")) {
      // Binary passthrough (audio/video playback). Stream the body and PRESERVE
      // the status (206 for range requests) + range/length headers, so seeking
      // works and iOS Safari will actually play it. Buffering + dropping these
      // was why <audio> showed "Error" on iPhone.
      const passHeaders: Record<string, string> = {
        "Content-Type": resType || "application/octet-stream",
      };
      for (const h of [
        "content-length",
        "content-range",
        "accept-ranges",
        "content-disposition",
        "cache-control",
        "etag",
        "last-modified",
      ]) {
        const v = res.headers.get(h);
        if (v) passHeaders[h] = v;
      }
      return new NextResponse(res.body, {
        status: res.status,
        headers: passHeaders,
      });
    }
    const resBody = await res.json().catch(() => ({}));
    return NextResponse.json(resBody, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("GET", req, ctx.params);
}

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("POST", req, ctx.params);
}

export async function PUT(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("PUT", req, ctx.params);
}

export async function PATCH(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("PATCH", req, ctx.params);
}

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("DELETE", req, ctx.params);
}
