/**
 * GET/POST/PATCH/DELETE /api/crm/[…path]
 *
 * Proxies every CRM request to the FastAPI gateway's /crm/* API. The browser
 * talks to the Next server, which holds the session and forwards an
 * authenticated request (internal bearer + X-User-Email) upstream — the same
 * shape as the tasks proxy at /api/tasks/[...path].
 *
 * ⚠️ This is not a convenience layer. `/crm` is gated by
 * `require_feature_router("crm")` and the gateway takes the acting identity
 * from `X-User-Email` only, so a page that fetched the gateway directly would
 * carry neither and 401 — the failure that took out every email-account
 * connection for six days (workbench/AGENTS.md, "Identity"). Nothing in this
 * app may point the browser at api.* .
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

function buildUpstreamUrl(path: string[], req: NextRequest): string {
  const base = `${GATEWAY_URL}/crm/${path.join("/")}`;
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
    // A pooled keep-alive socket can be closed by the gateway just as we
    // reuse it, failing the fetch spuriously (undici vs uvicorn's short
    // keep-alive). GETs are idempotent — retry once on network failure.
    let res: Response;
    try {
      res = await fetch(upstream, init);
    } catch (err) {
      if (method !== "GET") throw err;
      res = await fetch(upstream, {
        ...init,
        signal: AbortSignal.timeout(30_000),
      });
    }
    // ⚠️ **BYTES, not a string.** `Response.text()` is a UTF-8 *decode* (WHATWG
    // Encoding §BOM handling), and a UTF-8 decode strips a leading byte order
    // mark; re-encoding the decoded string into a new response then ships the
    // file without it. The gateway emits that BOM deliberately
    // (`gateway/csv_export.py`) because without it Excel on Windows reads the
    // CSV as the system code page and every non-ASCII name in the 3,993-row
    // Zoho backfill — "Café", "Sørensen", "₹", the Devanagari ones — arrives
    // mojibake. Measured on node v22: upstream `EF BB BF 4E 61 6D` relayed as
    // `4E 61 6D 65`. Reading the body as an `ArrayBuffer` and handing those
    // same bytes to `NextResponse` is what makes the downloaded file identical
    // to the one the endpoint produced. Fence: `src/lib/export.test.ts` runs
    // this proxy end to end over a BOM'd body.
    const buf = await res.arrayBuffer();
    // 204 and every other empty body: no content type to guess at, and
    // `NextResponse.json("")` would answer `""` where the caller expects
    // nothing.
    if (buf.byteLength === 0) return new NextResponse(null, { status: res.status });
    // ⚠️ The response type is the GATEWAY's, not a constant. This used to do
    // `res.json()` then `NextResponse.json(…)`, which is right for every route
    // but one: WS-26i-export's `GET /crm/export/{entity}.csv` answers
    // `text/csv` with a `Content-Disposition`, and re-encoding it as JSON made
    // a `{}` body arrive with a 200 — a download that looks like a working
    // request and saves no file. A refusal from that same endpoint is still
    // JSON and still arrives as JSON, because this reads what upstream
    // actually sent rather than what the route usually sends. (The Projects
    // proxy hit this first and carries the same arm; fence:
    // `src/lib/export.test.ts`.)
    //
    // The status is passed through verbatim: the CRM says a great deal with
    // its codes (422 for a bad sort key, a lost status with no reason or an
    // export wider than the row cap; 409 for a re-convert or a status still in
    // use), and a proxy that flattened them would leave the UI unable to
    // explain a refusal.
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
    // is the ONLY route to the export, so dropping it made the gateway's own
    // stated purpose ("for anything reading this programmatically")
    // unserveable.
    const exportRows = res.headers.get("x-export-rows");
    if (exportRows) headers["X-Export-Rows"] = exportRows;
    // ⚠️ A security header the gateway sets must survive this hop, or it does
    // not exist — this proxy is the only route to the bytes. The Projects
    // proxy is where this was found (its attachments now serve `inline`, so
    // the browser renders member-uploaded bytes on our origin), and the two
    // proxies carry the same tail. Fixed in both, and fenced for both by
    // `src/lib/export.test.ts`, the same way the BOM defect was.
    const noSniff = res.headers.get("x-content-type-options");
    if (noSniff) headers["X-Content-Type-Options"] = noSniff;
    return new NextResponse(buf, { status: res.status, headers });
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
