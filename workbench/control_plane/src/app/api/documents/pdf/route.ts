/**
 * POST /api/documents/pdf?filename=<name.pdf> — WS-27bm S8.
 *
 * Relays posted `text/html` to the gateway's `POST /documents/pdf`, which lays
 * it out as a PDF through `gateway/pdf_render.py`. The Reports download is the
 * caller: it formats a saved report's render ONCE (`lib/reportEmail.ts`,
 * `reportDocument`) and posts the HTML here.
 *
 * ⚠️ **BYTES both ways.** The request body goes up as an `ArrayBuffer` and the
 * PDF comes back as one. A PDF is binary, and `Response.text()` would decode
 * it as UTF-8 and break every byte above 0x7F (AGENTS.md rule 4, the BOM trap).
 *
 * The gateway owns every check: the identity, `text/html` only, the size cap
 * and the sanitizer. This proxy adds the session and nothing else.
 */
import { NextRequest, NextResponse } from "next/server";

import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const upstream = new URL(`${GATEWAY_URL}/documents/pdf`);
    const filename = req.nextUrl.searchParams.get("filename");
    if (filename) upstream.searchParams.set("filename", filename);

    const res = await fetch(upstream.toString(), {
      method: "POST",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": req.headers.get("content-type") ?? "",
      },
      body: await req.arrayBuffer(),
      signal: AbortSignal.timeout(60_000),
    });

    const headers: Record<string, string> = {
      "Content-Type": res.headers.get("content-type") ?? "application/octet-stream",
    };
    const disposition = res.headers.get("content-disposition");
    if (disposition) headers["Content-Disposition"] = disposition;
    return new Response(await res.arrayBuffer(), { status: res.status, headers });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
