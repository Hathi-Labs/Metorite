/**
 * The guards of `POST /api/artifacts/compile` that run before esbuild (task
 * 2432, F1). They live here, not in the route file, because a Next.js route
 * file may export only its handlers and its route config.
 *
 * ⚠️ The rate limit has no store of its own. The counter lives in the tenant
 * Redis wrapper (`acb_common.tenant_redis`), behind the gateway's
 * `POST /artifact-compile/slot` and `/charge`. Do not add a second limiter.
 *
 * The counter gives FAIRNESS between members. The box is protected by the
 * in-process guards of `compileArtifact` (one build at a time, one build for
 * each member, the bounded queue, the kill and the caps). So when the gateway
 * cannot count (a network error or a 5xx), the compile goes on with a warning
 * (fail open, task 2432 fix round, an overnight owner instruction). A 401 or
 * a 403 still refuses.
 */
import { NextResponse } from "next/server";

import { GATEWAY_URL, gatewayFetch, gatewayHeaders, NoIdentityError } from "@/lib/gateway";

/**
 * The largest request body: 1 MiB. The source cap is 64 KiB
 * (`COMPILE_LIMITS.maxSourceBytes`). JSON escapes can make the body much
 * bigger than the source, so the body cap leaves room for them.
 */
export const MAX_BODY_BYTES = 1024 * 1024;

export const SLOT_PATH = "/artifact-compile/slot";
export const CHARGE_PATH = "/artifact-compile/charge";

/** How long one call to the gateway counter may take. */
export const GATEWAY_TIMEOUT_MS = 5_000;

export const TOO_LARGE =
  "The request body is larger than 1 MiB. Send a smaller artifact source, at most " +
  "64 KB, and try again.";
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

/** One POST to the gateway counter as the member: a 5 s bound, and a retry on ECONNREFUSED only. */
async function counterCall(path: string, body?: unknown): Promise<Response> {
  return gatewayFetch(
    `${GATEWAY_URL}${path}`,
    {
      method: "POST",
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(GATEWAY_TIMEOUT_MS),
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    },
    // An INCR is safe to repeat on ECONNREFUSED: the gateway never got the
    // request. gatewayFetch retries a write on that error only.
    { retry: true },
  );
}

/**
 * Take one slot of the member's rate limit at the gateway. Null when the
 * build may go on, else the refusal to send.
 *
 * 429 refuses with the gateway's sentence. 401 and 403 refuse. A network
 * error, a timeout and a 5xx let the build go on, with a warning: the
 * counter is for fairness, and the in-process guards protect the box.
 */
export async function takeSlot(log: (line: string) => void = console.warn): Promise<NextResponse | null> {
  let res: Response;
  try {
    res = await counterCall(SLOT_PATH);
  } catch (err) {
    if (err instanceof NoIdentityError) return refuse(401, "unauthenticated", SIGN_IN);
    log(`[artifacts/compile] the build limit could not be checked, so the build goes on: ${String(err)}`);
    return null;
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
  log(`[artifacts/compile] the build limit answered ${res.status}, so the build goes on`);
  return null;
}

/**
 * Charge the member for a build that ran into the kill. It never throws and
 * never blocks the answer for long: a failed charge is only a warning.
 */
export async function chargeTimeout(log: (line: string) => void = console.warn): Promise<void> {
  try {
    const res = await counterCall(CHARGE_PATH, { kind: "timeout" });
    if (!res.ok) log(`[artifacts/compile] the timeout charge answered ${res.status}`);
  } catch (err) {
    log(`[artifacts/compile] the timeout charge failed: ${String(err)}`);
  }
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
