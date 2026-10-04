/**
 * GET/POST/PATCH/PUT/DELETE /api/email/[…path]
 *
 * Proxies all email requests to the FastAPI gateway /email/* path.
 * This avoids CORS issues — the browser talks to the Next.js server,
 * which forwards authenticated requests to the gateway.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
// The budget of each POST: 30s, or 120s for the AI/agent endpoints and for
// "Remove older mail from Metorite" (WS-17 EM-T6e, D1). The rule and its
// reasons live in that module, with a test beside it.
import { postTimeoutMs } from "./postTimeout";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  // Path-traversal guard: this [...path] catch-all must only ever hit the
  // gateway's /email/* surface. A segment of ".." (or one Next decodes to it
  // from %2e%2e) could otherwise normalise upstream to a SIBLING gateway route
  // (e.g. /v1/chat/completions, /actions/*) — and this proxy attaches the
  // internal Bearer token, so that would hand a workbench user agent-level
  // access to internal-only endpoints. Reject anything that isn't a plain
  // segment, then confirm the resolved URL is still under /email/.
  for (const seg of path) {
    if (
      !seg ||
      seg === "." ||
      seg === ".." ||
      seg.includes("/") ||
      seg.includes("\\")
    ) {
      throw new Error("Invalid email proxy path");
    }
  }
  const base = `${GATEWAY_URL}/email/${path.join("/")}`;
  const resolved = new URL(base);
  const root = new URL(`${GATEWAY_URL}/email/`);
  if (
    resolved.origin !== root.origin ||
    !resolved.pathname.startsWith("/email/")
  ) {
    throw new Error("Email proxy path escaped /email/");
  }
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const res = await gatewayFetch(upstream, {
      headers: await gatewayHeaders(),
      // Attachments can be large; allow more time than JSON calls.
      signal: AbortSignal.timeout(120_000),
    });
    const contentType = res.headers.get("content-type") ?? "";
    // Binary / non-JSON responses (e.g. attachment downloads) must be streamed
    // through untouched — parsing them as JSON corrupts the bytes and the
    // browser ends up with an empty object instead of the file.
    if (!contentType.includes("application/json")) {
      const headers = new Headers();
      for (const h of [
        "content-type",
        "content-disposition",
        "content-length",
        "cache-control",
      ]) {
        const v = res.headers.get(h);
        if (v) headers.set(h, v);
      }
      return new NextResponse(res.body, { status: res.status, headers });
    }
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
  const upstream = buildUpstreamUrl(path, req);
  try {
    const body = await req.json().catch(() => ({}));
    const res = await gatewayFetch(upstream, {
      method: "POST",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(postTimeoutMs(path)),
    });
    const resBody = await res.json().catch(() => ({}));
    return NextResponse.json(resBody, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const body = await req.json().catch(() => ({}));
    const res = await gatewayFetch(upstream, {
      method: "PATCH",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
    const resBody = await res.json().catch(() => ({}));
    return NextResponse.json(resBody, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const body = await req.json().catch(() => ({}));
    const res = await gatewayFetch(upstream, {
      method: "PUT",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
    const resBody = await res.json().catch(() => ({}));
    return NextResponse.json(resBody, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const res = await gatewayFetch(upstream, {
      method: "DELETE",
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(30_000),
    });
    // DELETE may return 204 with no body
    if (res.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    const resBody = await res.json().catch(() => ({}));
    return NextResponse.json(resBody, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
