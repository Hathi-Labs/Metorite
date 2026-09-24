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
 * and the sanitizer. This proxy adds the session, and it refuses a body over
 * the same cap before it holds all of it in memory (fix round 1).
 */
import { NextRequest, NextResponse } from "next/server";

import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/**
 * The gateway's `pdf_render.MAX_SOURCE_BYTES`. The gateway checks it again.
 * This copy only stops a large body at the edge, so a mismatch refuses early
 * or late, never wrongly.
 */
export const MAX_PDF_SOURCE_BYTES = 1_000_000;

const TOO_LARGE = { detail: "The document is too large for a PDF." };

/** The body, or null past the cap. It stops reading at the cap. */
async function readCapped(req: NextRequest): Promise<ArrayBuffer | null> {
  const declared = Number(req.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > MAX_PDF_SOURCE_BYTES) return null;
  if (!req.body) return new ArrayBuffer(0);
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_PDF_SOURCE_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const buffer = new ArrayBuffer(total);
  const body = new Uint8Array(buffer);
  let at = 0;
  for (const c of chunks) {
    body.set(c, at);
    at += c.byteLength;
  }
  return buffer;
}

export async function POST(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const upstream = new URL(`${GATEWAY_URL}/documents/pdf`);
    const filename = req.nextUrl.searchParams.get("filename");
    if (filename) upstream.searchParams.set("filename", filename);

    const body = await readCapped(req);
    if (body === null) return NextResponse.json(TOO_LARGE, { status: 413 });

    const res = await fetch(upstream.toString(), {
      method: "POST",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": req.headers.get("content-type") ?? "",
      },
      body,
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
