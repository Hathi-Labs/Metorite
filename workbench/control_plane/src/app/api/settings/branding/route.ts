/**
 * GET    /api/settings/branding — the organisation's logo, for the app shell
 * PUT    /api/settings/branding — replace it (admin-only, enforced upstream)
 * DELETE /api/settings/branding — remove it (admin-only, enforced upstream)
 *
 * The gateway is the authority. It re-derives the image format from the file's
 * magic bytes, re-checks every bound, and rebuilds the `data:` URI from what it
 * derived — so this route forwards rather than validates. The one thing it must
 * not do is decide for itself that an upload is fine.
 *
 * ## Why the error body IS relayed here, unlike the billing route
 *
 * These messages are written for the admin who just picked a file — "SVG logos
 * are not accepted, export a PNG at 2× the display size" is the entire value of
 * the response, and swallowing it for a generic "Upload failed" makes the panel
 * useless. They name no internal state: the vocabulary is file formats and
 * pixel counts, which the uploader already knows. Billing's upstream is written
 * for an operator and is a different judgement.
 */

import { NextRequest, NextResponse } from "next/server";

import {
  GATEWAY_URL,
  NoIdentityError,
  gatewayHeaders,
  requireIdentity,
  unauthenticated,
  gatewayFetch,
} from "@/lib/gateway";

// Resolves the signed-in member, so it can never be statically evaluated.
export const dynamic = "force-dynamic";

const GATEWAY_PATH = "/settings/branding";

/** Comfortably above the gateway's 128 KB raw ceiling, once base64-inflated. */
const MAX_BODY_BYTES = 256 * 1024;

/** Empty branding — what the shell renders our own mark from. */
const NO_BRANDING = { logo: null, updatedBy: "", updatedAt: "" };

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  try {
    const res = await gatewayFetch(`${GATEWAY_URL}${GATEWAY_PATH}`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(8_000),
      cache: "no-store",
    });
    if (res.ok) return NextResponse.json(await res.json());
  } catch {
    // Fall through. A gateway that is down or predates the branding route must
    // not stop the shell from rendering — it renders our mark instead, which is
    // exactly what an org with no logo gets. There is no degraded state to
    // announce here, because there is nothing the member could do about it.
  }

  return NextResponse.json(NO_BRANDING);
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  // Bound the read before parsing. Without this an unbounded body is buffered
  // into memory only to be rejected downstream, which is a cheap way to make a
  // Next.js server unwell from an authenticated account.
  //
  // ⚠️ `content-length` alone does NOT do this, and an earlier version of this
  // route claimed it did. A chunked-transfer-encoding request carries no
  // `content-length`, so the header check saw 0, passed, and `req.json()`
  // buffered the whole thing anyway — the header is a claim by the caller, and
  // the attack is a caller who declines to make it. The header check is kept as
  // a cheap early exit for honest clients; the STREAM is what actually bounds
  // it.
  const declared = Number(req.headers.get("content-length") ?? 0);
  if (declared > MAX_BODY_BYTES) {
    return NextResponse.json(
      { detail: "That image is too large." },
      { status: 413 },
    );
  }

  const text = await readBounded(req, MAX_BODY_BYTES);
  if (text === null) {
    return NextResponse.json({ detail: "That image is too large." }, { status: 413 });
  }

  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return NextResponse.json({ detail: "Body must be JSON" }, { status: 400 });
  }

  const logoBase64 = (body as Record<string, unknown> | null)?.logoBase64;
  if (typeof logoBase64 !== "string" || logoBase64.length === 0) {
    return NextResponse.json(
      { detail: "No image was included in the upload." },
      { status: 400 },
    );
  }
  if (logoBase64.length > MAX_BODY_BYTES) {
    return NextResponse.json({ detail: "That image is too large." }, { status: 413 });
  }

  return forward("PUT", JSON.stringify({ logoBase64 }));
}

export async function DELETE(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward("DELETE");
}

/**
 * Read the body, giving up the moment it exceeds `limit`.
 *
 * Returns `null` when the cap is passed. The point is that it stops PULLING —
 * the reader is cancelled, so the bytes after the limit are never accepted,
 * whether or not the caller declared a length. Anything that reads to
 * completion first and measures afterwards has already paid the cost it was
 * trying to avoid.
 */
async function readBounded(req: NextRequest, limit: number): Promise<string | null> {
  const reader = req.body?.getReader();
  if (!reader) return "";

  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }

  const joined = new Uint8Array(total);
  let at = 0;
  for (const c of chunks) {
    joined.set(c, at);
    at += c.byteLength;
  }
  return new TextDecoder().decode(joined);
}

async function forward(method: "PUT" | "DELETE", body?: string): Promise<NextResponse> {
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}${GATEWAY_PATH}`, {
      method,
      headers: {
        ...(await gatewayHeaders()),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body,
      // Longer than a read: this one carries a payload and writes a row.
      signal: AbortSignal.timeout(20_000),
    });

    const text = await res.text();
    if (!res.ok) {
      // Relay the upstream reason — see the note at the top of this file.
      let detail = `Could not save the logo (${res.status})`;
      try {
        const parsed = JSON.parse(text);
        if (typeof parsed?.detail === "string") detail = parsed.detail;
      } catch {
        // Non-JSON upstream error: keep the status-shaped fallback.
      }
      return NextResponse.json({ detail }, { status: res.status });
    }

    return NextResponse.json(text ? JSON.parse(text) : NO_BRANDING);
  } catch (err) {
    if (err instanceof NoIdentityError) return unauthenticated();
    return NextResponse.json(
      { detail: "Branding is temporarily unavailable." },
      { status: 503 },
    );
  }
}
