/**
 * GET/POST/PUT/PATCH/DELETE /api/projects/[…path]
 *
 * Proxies every Projects request to the FastAPI gateway's /projects/* API. The
 * browser talks to the Next server, which holds the session and forwards an
 * authenticated request (internal bearer + X-User-Email) upstream — the same
 * shape as the CRM proxy at /api/crm/[...path] and the tasks one before it.
 *
 * ⚠️ This is not a convenience layer. `/projects` is gated by
 * `require_feature_router("projects")` and the gateway takes the acting
 * identity from `X-User-Email` only, so a page that fetched the gateway
 * directly would carry neither and 401 — the failure that took out every
 * email-account connection for six days (workbench/AGENTS.md, "Identity").
 * Nothing in this app may point the browser at api.* .
 *
 * It matters more here than for most apps: `/projects` scopes DATA by grant,
 * not just navigation, so an unauthenticated upstream call would not merely
 * fail — under any future relaxation it would be a call with no member to
 * scope against.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  const base = `${GATEWAY_URL}/projects/${path.join("/")}`;
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
    const init: RequestInit = {
      method,
      headers: {
        ...(await gatewayHeaders()),
        // Set for the JSON path; the multipart branch below overwrites it
        // with the caller's own boundary-carrying type.
        ...(method === "GET" || method === "DELETE"
          ? {}
          : { "Content-Type": "application/json" }),
        // D-PM-20's write precondition. The gateway answers 412 when the row
        // moved since the caller read it. Dropped here, the check could never
        // fire from the browser (D79: an Undo must not overwrite a newer move).
        ...(req.headers.get("if-match")
          ? { "If-Match": req.headers.get("if-match") as string }
          : {}),
      },
      signal: AbortSignal.timeout(30_000),
    };
    if (method !== "GET" && method !== "DELETE") {
      // A multipart upload (WS-27i attachments) must reach the gateway
      // BYTE-FOR-BYTE. Re-parsing it as JSON is not merely lossy — `req.json()`
      // rejects, the catch below hands back `{}`, and the request arrives with
      // no file at all while still answering 201. The workflows app documents
      // the same trap for HMAC-signed webhook bodies (`workflows_app.md`
      // §3.3b); this is that trap on the upload path.
      const contentType = req.headers.get("content-type") ?? "";
      if (contentType.includes("application/json") || contentType === "") {
        const body = await req.json().catch(() => ({}));
        init.body = JSON.stringify(body);
      } else {
        init.body = await req.arrayBuffer();
        (init.headers as Record<string, string>)["Content-Type"] = contentType;
      }
    }
    // A pooled keep-alive socket can be closed by the gateway just as we reuse
    // it, failing the fetch spuriously (undici vs uvicorn's short keep-alive).
    // GETs are idempotent — retry once on network failure. A retried POST could
    // create a second project, so only GETs get the second attempt.
    let res: Response;
    try {
      res = await fetch(upstream, init);
    } catch (err) {
      if (method !== "GET") throw err;
      res = await fetch(upstream, { ...init, signal: AbortSignal.timeout(30_000) });
    }

    // ⚠️ **BYTES, not a string.** `Response.text()` is a UTF-8 *decode*, and a
    // UTF-8 decode strips a leading byte order mark; re-encoding the decoded
    // string then ships the file without it. `GET /projects/export/tasks.csv`
    // has answered with a BOM since WS-27ae and this proxy has been deleting
    // it ever since — a task titled "Café" reaches Excel on Windows as
    // "CafÃ©", which is the exact failure the BOM exists to prevent. Measured
    // on node v22: upstream `EF BB BF 4E 61 6D` relayed as `4E 61 6D 65`.
    // (Found while fixing the CRM's copy of this arm, WS-26i-export; fenced
    // for BOTH proxies by `src/lib/export.test.ts`.)
    const buf = await res.arrayBuffer();
    if (buf.byteLength === 0) return new NextResponse(null, { status: res.status });
    // ⚠️ The response type is the GATEWAY's, not a constant. This used to
    // stamp `application/json` on everything, which is right for every route
    // but one: WS-27ae's `GET /export/tasks.csv` answers `text/csv` with a
    // `Content-Disposition`, and relabelling it as JSON made the browser
    // treat a download as a document. A refusal from that same endpoint is
    // still JSON and still arrives as JSON, because this reads what upstream
    // actually sent rather than what the route usually sends.
    const upstreamType = res.headers.get("content-type");
    const headers: Record<string, string> = {
      "Content-Type": upstreamType ?? "application/json",
    };
    const disposition = res.headers.get("content-disposition");
    // Forwarded because the FILENAME is the server's to choose — dropping it
    // would leave the client inventing a second one that drifts.
    if (disposition) headers["Content-Disposition"] = disposition;
    // The gateway's honest row count beside the file. Forwarded because a
    // header no caller can reach is a header that does not exist: this proxy
    // is the ONLY route to the export.
    const exportRows = res.headers.get("x-export-rows");
    if (exportRows) headers["X-Export-Rows"] = exportRows;
    // ⚠️ **A security header the gateway sets is worthless unless it survives
    // this hop.** `GET /projects/attachments/{id}/{name}` answers
    // `Content-Disposition: inline` for a safelist of types, so the browser
    // RENDERS member-uploaded bytes on our origin with the session attached.
    // The gateway pairs that with `nosniff`, and this function builds its
    // headers from scratch — so until now the browser never saw it.
    //
    // Nothing in the app may point at `api.*` (see the note at the top), so
    // this proxy is the ONLY route to those bytes. A header dropped here is a
    // header that does not exist. Fenced by `src/lib/export.test.ts`, which
    // reads the header off the PROXY response and not off the gateway's.
    const noSniff = res.headers.get("x-content-type-options");
    if (noSniff) headers["X-Content-Type-Options"] = noSniff;
    return new NextResponse(buf, { status: res.status, headers });
  } catch (err) {
    return NextResponse.json(
      { detail: `Projects gateway unreachable: ${String(err)}` },
      { status: 502 }
    );
  }
}

// Every verb resolves the identity BEFORE forwarding. `gatewayHeaders()`
// throwing is what makes an unguarded call fail closed, but that throw lands in
// the catch below and answers 502 — telling a signed-out member the gateway is
// down when what they need is a sign-in. Pinned by
// `src/lib/gateway.test.ts` ("establishes who is asking wherever it reaches the
// gateway"), which caught exactly this omission here.

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
