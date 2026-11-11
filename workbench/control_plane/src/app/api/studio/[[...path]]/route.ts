/**
 * GET/POST/PUT/PATCH/DELETE /api/studio and /api/studio/[…path]
 *
 * Proxies the AI Studio requests to the FastAPI gateway /studio/* — the
 * browser talks to the Next.js server, which forwards authenticated requests
 * via the shared gateway helper (internal bearer + user identity).
 *
 * A copy of the workflows proxy for the prefix `/studio`, with PATCH added
 * for the skill state route (STU-F2b). It adds no identity and no path rule:
 * the gateway holds the real checks (the member, the feature, the
 * STUDIO_ORGS switch and the org of the bound session).
 *
 * STU-B2c-1: for a POST to `apps/<id>/actions/<action>` ONLY, it adds the
 * direct proof that `studioDirectHeaders` signs for the signed-in member. A
 * member's own Save is the approval of a write, and the gateway door checks the
 * proof. No browser header reaches the gateway. It forwards `Retry-After`, so
 * a 503 of a busy org keeps its wait.
 *
 * ⚠️ It signs only a request from a page of this ORIGIN (`sameOrigin`). The
 * session cookie is `SameSite=Lax`, so a page of another host of the same site
 * can make the browser send this POST. Such a POST goes on unsigned, and the
 * gateway answers 403 `approval_required`.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import {
  GATEWAY_URL,
  gatewayHeaders,
  requireIdentity,
  gatewayFetch,
  studioDirectHeaders,
} from "@/lib/gateway";
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
      headers: {
        ...(await gatewayHeaders(
          method === "GET" || method === "DELETE"
            ? {}
            : { "Content-Type": "application/json" }
        )),
        ...(sameOrigin(req) ? studioDirectHeaders(me.email, method, path) : {}),
      },
      signal: AbortSignal.timeout(60_000),
    };
    if (method !== "GET" && method !== "DELETE") {
      const body = await req.json().catch(() => ({}));
      init.body = JSON.stringify(body);
    } else if (method === "DELETE") {
      // The delete of a package carries its confirm token in the body (A5c-1,
      // BETA-1). A DELETE with no body, or with a body that is not JSON, goes on with none.
      const raw = await req.text().catch(() => "");
      let body: unknown;
      try { body = raw.trim() ? JSON.parse(raw) : undefined; } catch { body = undefined; }
      if (body !== undefined) {
        init.body = JSON.stringify(body);
        init.headers = { ...(init.headers as Record<string, string>), "Content-Type": "application/json" };
      }
    }
    // A refused connect and a stale keep-alive socket are retried inside
    // `gatewayFetch` (H-194). Do not add a second retry here: it would
    // double the wait and could replay a write.
    const res = await gatewayFetch(upstream, init);
    if (res.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    const resBody = await res.json().catch(() => ({}));
    const answer = NextResponse.json(resBody, { status: res.status });
    const wait = res.headers.get("Retry-After");
    if (wait) answer.headers.set("Retry-After", wait);
    return answer;
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
