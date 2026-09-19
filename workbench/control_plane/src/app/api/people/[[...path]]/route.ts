/**
 * GET/PATCH/POST /api/people/[[…path]]
 *
 * ⚠️ **An OPTIONAL catch-all, and that is load-bearing.** The directory LIST
 * is `GET /people/` upstream, so the browser asks for `/api/people` with NO
 * path segment. A required catch-all (`[...path]`) matches one segment or
 * more and never the bare path, so Next answered its own 404 HTML page, the
 * client called `JSON.parse` on `<!DOCTYPE html>` and every visit to the
 * directory showed "Unexpected token '<'". The list had never once loaded.
 *
 * The second symptom was worse than the first: `can_manage` rides on that
 * same response, so the "Add person" control never drew and an admin had no
 * way to add anybody. Fenced by `people/lib/proxyRoute.test.ts`.
 *
 * Proxies People Center requests to the FastAPI gateway's /people/* API. The
 * browser talks to the Next server, which holds the session and forwards an
 * authenticated request (internal bearer + X-User-Email) upstream — the same
 * shape as the Projects and CRM proxies.
 *
 * ⚠️ This is not a convenience layer. `/people` is gated by
 * `require_feature_router("people")` and the gateway takes the acting identity
 * from `X-User-Email` only, so a page that fetched the gateway directly would
 * carry neither and 401. Nothing in this app may point the browser at api.* .
 *
 * **It was GET-only until WS-28g, and the reason it stopped being GET-only is
 * the point.** The original rule was not "reads are safer" — it was that the
 * gateway's `/people` router served no writes, so a verb added here would
 * forward to an endpoint that does not exist and mint a second, hollow write
 * path that the first person to find would reasonably assume worked. WS-28g
 * built the endpoints (`PATCH /people/{id}` and `POST /people/{id}/resume`,
 * the self-service door in `routes/people/profile.py`), so the forwarding is
 * now real. Admin-only writes still go to `/api/tasks/people`, where they have
 * always lived — see `people/lib/write.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[] | undefined, req: NextRequest): string {
  // `path` is undefined for the bare `/api/people` — the directory list. The
  // gateway serves that as `GET /people/`, trailing slash and all, which is
  // exactly what an empty join produces here. Do not "tidy" the slash away.
  const base = `${GATEWAY_URL}/people/${(path ?? []).join("/")}`;
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE",
  req: NextRequest,
  params: Promise<{ path?: string[] }>
): Promise<NextResponse> {
  const { path } = await params;
  const upstream = buildUpstreamUrl(path, req);
  try {
    const reqType = req.headers.get("content-type") ?? "";
    // The CV upload is multipart and must pass through byte-exact with its
    // boundary — rebuilding it as JSON would silently strip the file.
    const isMultipart = reqType.startsWith("multipart/form-data");
    const init: RequestInit = {
      method,
      headers: {
        ...(await gatewayHeaders()),
        ...(method === "GET" || method === "DELETE"
          ? {}
          : { "Content-Type": isMultipart ? reqType : "application/json" }),
      },
      signal: AbortSignal.timeout(30_000),
    };
    if (method !== "GET" && method !== "DELETE") {
      init.body = isMultipart
        ? Buffer.from(await req.arrayBuffer())
        : JSON.stringify(await req.json().catch(() => ({})));
    }
    // A pooled keep-alive socket can be closed by the gateway just as we reuse
    // it (undici vs uvicorn's short keep-alive). **Only reads are retried**: a
    // PATCH replayed after an ambiguous failure is a second write, and the one
    // thing worse than a save that failed is a save that happened twice.
    let res: Response;
    try {
      res = await fetch(upstream, init);
    } catch (err) {
      if (method !== "GET") throw err;
      res = await fetch(upstream, { ...init, signal: AbortSignal.timeout(30_000) });
    }
    const text = await res.text();
    if (!text) return new NextResponse(null, { status: res.status });
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `People gateway unreachable: ${String(err)}` },
      { status: 502 }
    );
  }
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
) {
  // Identity is resolved BEFORE forwarding. `gatewayHeaders()` throwing is what
  // makes an unguarded call fail closed, but that throw lands in the catch
  // below and answers 502 — telling a signed-out member the gateway is down
  // when what they need is a sign-in. Pinned by `src/lib/gateway.test.ts`.
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("GET", req, ctx.params);
}

export async function PATCH(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("PATCH", req, ctx.params);
}

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("POST", req, ctx.params);
}

/** WS-28p: `PUT /people/schedule` — the org's working week, admin-gated upstream. */
export async function PUT(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("PUT", req, ctx.params);
}

/** WS-28q: removing a display image. No body, so nothing is forwarded. */
export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> }
) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("DELETE", req, ctx.params);
}
