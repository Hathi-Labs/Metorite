// ── The one fetch from the BFF to the gateway (H-194) ──────────────────────
//
// Every server-side call from this app to the FastAPI gateway goes through
// `gatewayFetch`. It is `fetch` plus ONE behaviour: while the gateway restarts,
// a connection that the gateway never accepted is tried again, for a bounded
// time, instead of failing the member's request.
//
// WHY IT EXISTS
// -------------
// The workbench server calls the gateway directly on 127.0.0.1:8080. It does
// not go through Caddy, so Caddy's `lb_try_duration` hold (H-60) does not
// cover it. Each deploy restarts the gateway, and uvicorn binds its port only
// after application startup completes. Measured on the deploy of 2026-09-27
// 21:06: the old process stopped at 21:06:18.48 and the new one listened at
// 21:06:28.77. For those 10.3 s every connect was refused, and the browser's
// 6 s `/chat/active-sessions` poll lost two answers in a row.
//
// THE RULES
// ---------
// 1. Retry ONLY a failure where no response arrived. A response of any status,
//    a 503 included, is the gateway's answer and goes back to the caller.
// 2. GET, HEAD and OPTIONS retry on ECONNREFUSED, ECONNRESET and
//    UND_ERR_SOCKET ("other side closed"). Replaying a read is harmless.
// 3. Every other method retries ONLY on ECONNREFUSED. A refusal proves the
//    gateway never received the request. A reset does not prove that: the
//    gateway can have read the body and started the write, and a replayed
//    POST is then a second write.
// 4. A body that cannot be replayed is never retried. A string, a Buffer, an
//    ArrayBuffer, a Blob, URLSearchParams and FormData are rebuilt by `fetch`
//    on each attempt. A ReadableStream is consumed by the first attempt, so a
//    second attempt would send an empty or broken body.
// 5. The retry window is bounded by GATEWAY_RETRY.deadlineMs, which must stay
//    below Caddy's `lb_try_duration` (30 s). `gatewayFetch.test.ts` reads the
//    Caddyfile and fails if it does not. The caller's `init.signal` also
//    stops the retries.
// 6. A streamed response is never retried. `fetch` resolves when the headers
//    arrive, and this function returns at that moment. A failure while the
//    caller reads the body happens after this function has returned, so it
//    reaches the caller as it always did.
// 7. Every attempt sends the SAME `init`: the same headers, and so the same
//    internal bearer and the same member identity (R5). Nothing here reads or
//    re-derives an identity.
//
// THE LOG
// -------
// A request that needs a retry writes one `[gateway] retry` line when it
// first fails, and one `[gateway] recovered` or `[gateway] gave up` line at
// the end. So `journalctl -u acb-workbench | grep '\[gateway\]'` shows the
// restart window of every deploy.
//
// Fence: `src/lib/gatewayFetch.test.ts` proves the rules against a real local
// HTTP server. `src/lib/gateway.test.ts` fails if a module that reaches the
// gateway calls the bare `fetch` again.

/** The retry window. `deadlineMs` must stay below Caddy's 30 s hold. */
export const GATEWAY_RETRY = Object.freeze({
  deadlineMs: 25_000,
  firstDelayMs: 250,
  maxDelayMs: 1_000,
});

export interface GatewayRetryOptions {
  deadlineMs?: number;
  firstDelayMs?: number;
  maxDelayMs?: number;
  /** For tests only. Defaults to the global `fetch`, read at call time. */
  fetchImpl?: typeof fetch;
  /** For tests only. Defaults to `console.warn`. */
  log?: (line: string) => void;
}

const REFUSED = "ECONNREFUSED";
const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const IDEMPOTENT_CODES = new Set([REFUSED, "ECONNRESET", "UND_ERR_SOCKET"]);

function codeOf(cause: unknown): string | null {
  if (!cause || typeof cause !== "object") return null;
  // With `autoSelectFamily`, Node tries each address of a host name and
  // reports an AggregateError. It counts as a refusal only when EVERY address
  // refused.
  if (cause instanceof AggregateError) {
    const codes = cause.errors.map(codeOf);
    return codes.length > 0 && codes.every((c) => c === REFUSED) ? REFUSED : null;
  }
  const code = (cause as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

/**
 * The connection-level error code of a failed `fetch`, or null.
 *
 * Node's `fetch` rejects with `TypeError("fetch failed")` and puts the socket
 * error in `cause`. An abort, a timeout or a thrown header helper has no such
 * cause, and so is never retried.
 */
export function connectFailureCode(err: unknown): string | null {
  if (!err || typeof err !== "object") return null;
  return codeOf((err as { cause?: unknown }).cause);
}

/** True when `fetch` can send this body again on a second attempt. */
export function isReplayableBody(body: RequestInit["body"]): boolean {
  if (body === undefined || body === null) return true;
  if (typeof body === "string") return true;
  if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) return true;
  if (typeof Blob !== "undefined" && body instanceof Blob) return true;
  if (body instanceof URLSearchParams) return true;
  if (typeof FormData !== "undefined" && body instanceof FormData) return true;
  return false;
}

function mayRetry(method: string, code: string | null, replayable: boolean): boolean {
  if (!code || !replayable) return false;
  if (IDEMPOTENT_METHODS.has(method)) return IDEMPOTENT_CODES.has(code);
  return code === REFUSED;
}

function routeOf(input: string | URL): string {
  try {
    // The path only. A query string can carry a search term or an address.
    return new URL(String(input)).pathname;
  } catch {
    return "?";
  }
}

function sleep(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * `fetch` to the gateway, with a bounded retry while the gateway restarts.
 *
 * Use it wherever this app calls the gateway, with the same arguments you
 * would give `fetch`. See the rules at the top of this file.
 */
export async function gatewayFetch(
  input: string | URL,
  init: RequestInit = {},
  opts: GatewayRetryOptions = {}
): Promise<Response> {
  const deadlineMs = opts.deadlineMs ?? GATEWAY_RETRY.deadlineMs;
  const maxDelayMs = opts.maxDelayMs ?? GATEWAY_RETRY.maxDelayMs;
  let delay = opts.firstDelayMs ?? GATEWAY_RETRY.firstDelayMs;
  const doFetch = opts.fetchImpl ?? ((i: RequestInfo | URL, r?: RequestInit) => globalThis.fetch(i, r));
  const log = opts.log ?? ((line: string) => console.warn(line));

  const method = (init.method ?? "GET").toUpperCase();
  const replayable = isReplayableBody(init.body);
  const started = Date.now();
  let attempt = 1;

  for (;;) {
    try {
      const res = await doFetch(input, init);
      if (attempt > 1) {
        log(
          `[gateway] recovered: ${method} ${routeOf(input)} after ${attempt} attempts, ${Date.now() - started} ms`
        );
      }
      return res;
    } catch (err) {
      const code = connectFailureCode(err);
      const elapsed = Date.now() - started;
      const retry =
        mayRetry(method, code, replayable) &&
        !init.signal?.aborted &&
        elapsed + delay <= deadlineMs;
      if (!retry) {
        if (attempt > 1) {
          log(
            `[gateway] gave up: ${method} ${routeOf(input)} after ${attempt} attempts, ${elapsed} ms (${code ?? "no code"})`
          );
        }
        throw err;
      }
      if (attempt === 1) {
        log(
          `[gateway] retry: ${method} ${routeOf(input)} failed with ${code}; retrying for up to ${deadlineMs} ms`
        );
      }
      await sleep(delay, init.signal);
      delay = Math.min(delay * 2, maxDelayMs);
      attempt += 1;
    }
  }
}
