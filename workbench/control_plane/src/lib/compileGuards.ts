/**
 * The guards of `POST /api/artifacts/compile` that run before esbuild (task
 * 2432, F1). They live here, not in the route file, because a Next.js route
 * file may export only its handlers and its route config.
 *
 * ⚠️ The rate limit has no store of its own. The counter lives in the tenant
 * Redis wrapper (`acb_common.tenant_redis`), behind the gateway's
 * `POST /artifact-compile/slot`. Do not add a second limiter here.
 */
import { NextResponse } from "next/server";

import { COMPILE_LIMITS } from "@/lib/compileArtifact";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, NoIdentityError } from "@/lib/gateway";

/**
 * The largest request body. The source cap is 256 KiB. JSON escapes can make
 * the body bigger than the source, so the body cap leaves room for that.
 */
export const MAX_BODY_BYTES = 4 * COMPILE_LIMITS.maxSourceBytes;

export const SLOT_PATH = "/artifact-compile/slot";

export const TOO_LARGE =
  `The artifact source is larger than ${MAX_BODY_BYTES / 1024} KB. Split the ` +
  "artifact up or trim it, and send it again.";
export const LIMIT_UNAVAILABLE =
  "Metorite could not check the artifact build limit. Try again in a moment.";
export const RATE_LIMITED =
  "You built many artifacts in a short time. Wait a few minutes and try again.";
export const NOT_JSON = "The request body must be JSON with a `code` string. Send it again.";
export const NO_CODE =
  "The request body has no `code` string. Send the component source as `code`.";
export const SIGN_IN = "Sign in to continue";

/** The one shape of a refusal: `{ ok: false, error, code }`. */
export function refuse(
  status: number,
  code: string,
  error: string,
  retryAfter?: string | null,
): NextResponse {
  return NextResponse.json(
    { ok: false, error, code },
    { status, headers: retryAfter ? { "Retry-After": retryAfter } : undefined },
  );
}

/** A declared Content-Length over the cap. A missing one passes to the read. */
export function declaredTooLarge(request: Request): boolean {
  const declared = request.headers.get("content-length");
  return declared !== null && /^\d+$/.test(declared) && Number(declared) > MAX_BODY_BYTES;
}

/**
 * Take one slot of the member's rate limit at the gateway. Null when the
 * build may go on, else the refusal to send. Fails closed: a gateway that
 * does not answer is a 503, because the limit guards the box.
 */
export async function takeSlot(): Promise<NextResponse | null> {
  let res: Response;
  try {
    res = await gatewayFetch(`${GATEWAY_URL}${SLOT_PATH}`, {
      method: "POST",
      headers: await gatewayHeaders(),
      cache: "no-store",
    });
  } catch (err) {
    if (err instanceof NoIdentityError) return refuse(401, "unauthenticated", SIGN_IN);
    return refuse(503, "limit_unavailable", LIMIT_UNAVAILABLE, "2");
  }
  if (res.ok) return null;
  const body = (await res.json().catch(() => null)) as
    | { detail?: { message?: unknown } | string }
    | null;
  const detail = body?.detail;
  const said =
    typeof detail === "object" && typeof detail?.message === "string" ? detail.message : null;
  if (res.status === 429) {
    return refuse(429, "rate_limited", said ?? RATE_LIMITED, res.headers.get("retry-after"));
  }
  if (res.status === 401 || res.status === 403) {
    return refuse(res.status, "not_allowed", said ?? SIGN_IN);
  }
  return refuse(503, "limit_unavailable", LIMIT_UNAVAILABLE, "2");
}

/**
 * The body as text, read as a stream and stopped past `cap` bytes. Null when
 * the body is over the cap. The read never holds more than `cap` bytes, so a
 * missing or false Content-Length cannot get a large body into memory.
 */
export async function readCapped(request: Request, cap: number): Promise<string | null> {
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > cap) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString("utf8");
}
