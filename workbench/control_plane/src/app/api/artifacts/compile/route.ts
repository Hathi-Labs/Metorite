/**
 * POST /api/artifacts/compile
 *
 * Bundles an agent-authored React component into one self-contained IIFE that
 * the artifact sandbox can run offline (see lib/compileArtifact).
 *
 * The bundling itself is the security-sensitive part and is handled there: the
 * source is untrusted model output, and only an allowlisted React runtime may be
 * imported, so no caller-supplied path ever reaches the filesystem resolver.
 *
 * Compilation is deterministic for a given input, so responses are cached
 * immutably by content hash — a re-render of the same artifact (theme flip, tab
 * switch, chat scroll-back) never recompiles.
 *
 * ── The guards in front of esbuild (task 2432, F1) ─────────────────────────
 * Any signed-in member reaches this route, and esbuild runs on the box that
 * serves every tenant. In order, before esbuild starts (lib/compileGuards):
 *   1. a member, or 401;
 *   2. a declared Content-Length over the body cap, or 413;
 *   3. one slot of the member's rate limit, from the gateway's
 *      `POST /artifact-compile/slot`. The counter lives in the tenant Redis
 *      wrapper, so this route keeps no limiter of its own. Over the limit:
 *      429 `rate_limited`. The counter is for fairness, so when the gateway
 *      cannot count, the build goes on (fail open, fix round);
 *   4. the body, read as a stream and cut at the body cap, or 413. A missing
 *      or false Content-Length cannot get past this read;
 *   5. JSON with a `code` string, or 400.
 * Then `compileArtifact` adds one build at a time, one build for each member
 * (a second one: 429 `member_busy`), a queue bounded by count and by wait
 * (full: 429 `busy`), a timeout that kills esbuild, and a cap on the output.
 * A build that ran into the kill is charged 20 more units at the gateway
 * (`POST /artifact-compile/charge`), so one member cannot keep the slot busy.
 *
 * Each refusal is `{ ok: false, error, code }`. `error` says what happened and
 * what to do next, because SandboxedReact shows it as it is.
 */
import { createHash } from "node:crypto";
import { NextResponse } from "next/server";

import { compileArtifact } from "@/lib/compileArtifact";
import {
  chargeTimeout,
  declaredTooLarge,
  MAX_BODY_BYTES,
  NO_CODE,
  NOT_JSON,
  readCapped,
  refuse,
  takeSlot,
  TOO_LARGE,
} from "@/lib/compileGuards";
import { requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";
// esbuild is a native binary — it cannot run on the edge runtime.
export const runtime = "nodejs";

export async function POST(request: Request): Promise<NextResponse> {
  // Bundling is CPU-bound and caller-driven, so it needs a member behind
  // it even though `/api/artifacts/` is not in the proxy's public list.
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  if (declaredTooLarge(request)) return refuse(413, "too_large", TOO_LARGE);

  const limited = await takeSlot();
  if (limited) return limited;

  const text = await readCapped(request, MAX_BODY_BYTES);
  if (text === null) return refuse(413, "too_large", TOO_LARGE);

  let code: unknown;
  try {
    ({ code } = JSON.parse(text) as { code?: unknown });
  } catch {
    return refuse(400, "not_json", NOT_JSON);
  }
  if (typeof code !== "string") return refuse(400, "no_code", NO_CODE);

  const result = await compileArtifact(code, {}, { owner: me.email });
  if (!result.ok) {
    if (result.reason === "busy") return refuse(429, "busy", result.error, "5");
    if (result.reason === "member_busy") return refuse(429, "member_busy", result.error, "2");
    // A build that ran into the kill held the one build slot for the whole
    // timeout. It costs the member 20 more units of the rate limit.
    if (result.reason === "timeout") await chargeTimeout();
    // A compile error, a timeout or an oversized bundle is the source's fault,
    // the agent's to fix, not a server fault — 200 with ok:false so the client
    // renders the diagnostics instead of a network error.
    return NextResponse.json(result);
  }
  const etag = `"${createHash("sha256").update(result.js).digest("hex").slice(0, 32)}"`;
  return NextResponse.json(result, {
    headers: { ETag: etag, "Cache-Control": "private, max-age=31536000, immutable" },
  });
}
