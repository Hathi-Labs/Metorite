/**
 * GET/POST/DELETE /api/whatsapp/[…path]
 *
 * Proxies WhatsApp requests to the FastAPI gateway /whatsapp/* surface, so the
 * browser talks to the Next server (no CORS) and the internal Bearer token +
 * X-User-Email are attached server-side. Mirrors the email proxy, including its
 * path-traversal guard.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  // Same guard as the email proxy: this catch-all attaches the internal token,
  // so a ".." segment must never let the resolved URL escape /whatsapp/ into a
  // sibling gateway route (e.g. /v1/*, /actions/*).
  for (const seg of path) {
    if (
      !seg ||
      seg === "." ||
      seg === ".." ||
      seg.includes("/") ||
      seg.includes("\\")
    ) {
      throw new Error("Invalid whatsapp proxy path");
    }
  }
  const base = `${GATEWAY_URL}/whatsapp/${path.join("/")}`;
  const resolved = new URL(base);
  const root = new URL(`${GATEWAY_URL}/whatsapp/`);
  if (
    resolved.origin !== root.origin ||
    !resolved.pathname.startsWith("/whatsapp/")
  ) {
    throw new Error("WhatsApp proxy path escaped /whatsapp/");
  }
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  req: NextRequest,
  path: string[],
  method: string
): Promise<NextResponse> {
  let upstream: string;
  try {
    upstream = buildUpstreamUrl(path, req);
  } catch {
    return NextResponse.json({ detail: "invalid path" }, { status: 400 });
  }
  const headers = await gatewayHeaders();
  const init: RequestInit = {
    method,
    headers,
    signal: AbortSignal.timeout(30_000),
  };
  if (method !== "GET" && method !== "DELETE") {
    const body = await req.text();
    if (body) {
      init.body = body;
      headers["Content-Type"] = "application/json";
    }
  }
  try {
    const res = await gatewayFetch(upstream, init);
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch {
    return NextResponse.json(
      { detail: "gateway unreachable" },
      { status: 502 }
    );
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  return forward(req, path, "GET");
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  return forward(req, path, "POST");
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  return forward(req, path, "PATCH");
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { path } = await params;
  return forward(req, path, "DELETE");
}
