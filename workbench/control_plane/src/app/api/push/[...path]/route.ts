/**
 * GET/POST/DELETE /api/push/[…path] (WS-66 PN-1).
 *
 * Proxies the web push routes to the gateway's `/push/*`, as
 * `api/tasks/[...path]` does for `/tasks/*`: the browser talks to this server,
 * which forwards the request with the internal bearer and the member's
 * identity headers (`gatewayHeaders()`). The member and the org come from that
 * session at the gateway, never from the body.
 *
 * One change from the tasks proxy: a DELETE carries its JSON body, because
 * `DELETE /push/subscriptions` names the endpoint of this device there, and
 * an endpoint in the query would land in every access log.
 *
 * Spec: `project-docs/specs/calendar_events_reminders.md` §10.2 item 6.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  const base = `${GATEWAY_URL}/push/${path.join("/")}`;
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  method: "GET" | "POST" | "DELETE",
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
        ...(method === "GET" ? {} : { "Content-Type": "application/json" }),
      },
      signal: AbortSignal.timeout(30_000),
    };
    if (method !== "GET") {
      const body = await req.json().catch(() => ({}));
      init.body = JSON.stringify(body);
    }
    // `gatewayFetch` retries a refused connect and a closed keep-alive socket
    // by the rules in `lib/gatewayFetch.ts` (H-194). Do not add a second retry.
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

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("DELETE", req, ctx.params);
}
