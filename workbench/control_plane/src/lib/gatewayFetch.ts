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
// 3. A write (every other method) does NOT retry, unless the caller passes
//    `{ retry: true }`. Then it retries ONLY on ECONNREFUSED. A refusal proves
//    the gateway never received the request. A reset does not prove that: the
//    gateway can have read the body and started the write, and a replayed
//    POST is then a second write.
//    Why writes must opt in: several writes to one record can queue up
//    during a restart, and each one connects on its own backoff tick, in no
//    fixed order. An older copy can then land last. A draft auto-save is the
//    example: saves pile up with no draft id yet, and each one makes a new
//    provider draft. A write that failed at once, as before, is safer.
// 4. A body that cannot be replayed is never retried. A string, a Buffer, an
//    ArrayBuffer, a Blob, URLSearchParams and FormData are rebuilt by `fetch`
//    on each attempt. A ReadableStream is consumed by the first attempt, so a
//    second attempt would send an empty or broken body.
// 5. The retry window is bounded by GATEWAY_RETRY.deadlineMs, which must stay
//    below Caddy's `lb_try_duration` (30 s). `gatewayFetch.test.ts` reads the
//    Caddyfile and fails if it does not. Caddy sets no response timeout, so
//    the gateway's own answer time comes on top, as it always did. The env
//    `GATEWAY_RETRY_DEADLINE_MS` can shorten the window, and `0` turns the
//    retry off (see retryDeadlineMs). A call can pass `{ retry: false }`.
//    ⚠️ The cost: while the gateway is DOWN, not restarting, each call waits
//    the full window before it fails, where it used to fail at once.
// 6. A streamed response is never retried. `fetch` resolves when the headers
//    arrive, and this function returns at that moment. A failure while the
//    caller reads the body happens after this function has returned, so it
//    reaches the caller as it always did.
// 7. Every attempt sends the SAME `init`: the same headers, and so the same
//    internal bearer and the same member identity (R5). Nothing here reads or
//    re-derives an identity.
// 8. The caller's `init.signal` is a budget for the gateway's ANSWER, not for
//    the restart. Most calls carry `AbortSignal.timeout(4_000 to 10_000)`, and
//    the gateway is cold for 10 to 23 s, so a timeout that counted the
//    restart would end most retries early. So when the caller's signal fires
//    with a `TimeoutError` inside the retry window, the retry goes on. Each
//    later try then gets a new timeout of the same length (at least 1 s),
//    which starts when that try starts. Any other abort, such as the member
//    closing the page, ends the request at once. After a timeout is deferred,
//    a later abort of the caller's signal is not seen, and the deadline of
//    rule 5 is what ends the retry.
//
// THE LOG
// -------
// A request that needs a retry writes one `[gateway] retry` line when it
// first fails, and one `[gateway] recovered` or `[gateway] gave up` line at
// the end. `gave up` names why: the last error code, `aborted`, or the name
// of the error that ended the last try, such as `TimeoutError`. So `journalctl -u acb-workbench | grep '\[gateway\]'` shows the
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

/**
 * The retry window for this process, in ms.
 *
 * `GATEWAY_RETRY_DEADLINE_MS` can make it SHORTER, never longer: a value
 * above GATEWAY_RETRY.deadlineMs is capped, so the env cannot push the window
 * past Caddy's hold. `0` turns the retry off. Two uses: the browser suite runs
 * with no gateway at all and sets `0` (playwright.config.ts), and an operator
 * can set `0` on the box to turn the retry off without a code change.
 */
export function retryDeadlineMs(env: string | undefined = process.env.GATEWAY_RETRY_DEADLINE_MS): number {
  const n = Number(env);
  if (env === undefined || env.trim() === "" || !Number.isFinite(n) || n < 0) {
    return GATEWAY_RETRY.deadlineMs;
  }
  return Math.min(n, GATEWAY_RETRY.deadlineMs);
}

export interface GatewayRetryOptions {
  /**
   * Which requests retry (rule 3).
   *
   * - Not set: a read retries, and a write does not.
   * - `true`: a write also retries, on ECONNREFUSED only. Pass it only where
   *   no other write to the same record can be in flight, and where the
   *   write is the newest copy. The final chat checkpoint is the example.
   * - `false`: nothing retries.
   */
  retry?: boolean;
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

function mayRetry(
  method: string,
  code: string | null,
  replayable: boolean,
  retryWrites: boolean
): boolean {
  if (!code || !replayable) return false;
  if (IDEMPOTENT_METHODS.has(method)) return IDEMPOTENT_CODES.has(code);
  return retryWrites && code === REFUSED;
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
  const deadlineMs = opts.retry === false ? 0 : (opts.deadlineMs ?? retryDeadlineMs());
  const maxDelayMs = opts.maxDelayMs ?? GATEWAY_RETRY.maxDelayMs;
  let delay = opts.firstDelayMs ?? GATEWAY_RETRY.firstDelayMs;
  const doFetch = opts.fetchImpl ?? ((i: RequestInfo | URL, r?: RequestInit) => globalThis.fetch(i, r));
  const log = opts.log ?? ((line: string) => console.warn(line));

  const method = (init.method ?? "GET").toUpperCase();
  const route = routeOf(input);
  const replayable = isReplayableBody(init.body);
  const started = Date.now();
  let attempt = 1;
  let retrying = false;

  // The caller's timeout (rule 8). Null until it fires inside the restart
  // window. Then it holds the time the caller gave, and each later try gets
  // a new timeout of that length, which starts when that try starts.
  let budgetMs: number | null = null;
  const takeBudget = () => {
    budgetMs = Math.max(Date.now() - started, MIN_BUDGET_MS);
  };
  const tryInit = (): RequestInit =>
    budgetMs === null ? init : { ...init, signal: AbortSignal.timeout(budgetMs) };

  const gaveUp = (why: string) => {
    if (retrying) {
      log(`[gateway] gave up: ${method} ${route} after ${attempt} attempts, ${Date.now() - started} ms (${why})`);
    }
  };

  for (;;) {
    try {
      const res = await doFetch(input, tryInit());
      if (retrying) {
        log(`[gateway] recovered: ${method} ${route} after ${attempt} attempts, ${Date.now() - started} ms`);
      }
      return res;
    } catch (err) {
      const code = connectFailureCode(err);
      // The caller's timeout fired while a try was in flight, after an earlier
      // try failed to connect. A read is safe to send again, so the timeout
      // is deferred. A write is not, because this try may have connected.
      const lateTimeout =
        retrying &&
        budgetMs === null &&
        IDEMPOTENT_METHODS.has(method) &&
        isTimeout(err) &&
        isTimeout(init.signal?.reason);
      if (lateTimeout) {
        takeBudget();
      } else if (!mayRetry(method, code, replayable, opts.retry === true)) {
        const clientGone = init.signal?.aborted && !isTimeout(init.signal.reason);
        gaveUp(clientGone ? "aborted" : (code ?? errorName(err)));
        throw err;
      }
      // The connect failed, and the caller's signal fired at about the same
      // time. A timeout is deferred. Any other abort ends the request.
      if (budgetMs === null && init.signal?.aborted) {
        if (!isTimeout(init.signal.reason)) {
          gaveUp("aborted");
          throw err;
        }
        takeBudget();
      }
      if (Date.now() - started + delay > deadlineMs) {
        gaveUp(code ?? errorName(err));
        throw err;
      }
      if (!retrying) {
        retrying = true;
        log(`[gateway] retry: ${method} ${route} failed with ${code}; retrying for up to ${deadlineMs} ms`);
      }
      try {
        await sleep(delay, budgetMs === null ? init.signal : null);
      } catch (reason) {
        if (!isTimeout(reason)) {
          gaveUp("aborted");
          throw reason;
        }
        takeBudget();
      }
      delay = Math.min(delay * 2, maxDelayMs);
      attempt += 1;
    }
  }
}

/** The shortest answer time a deferred caller timeout can give a try. */
const MIN_BUDGET_MS = 1_000;

function isTimeout(reason: unknown): boolean {
  return (reason as { name?: unknown } | null)?.name === "TimeoutError";
}

function errorName(err: unknown): string {
  const name = (err as { name?: unknown } | null)?.name;
  return typeof name === "string" ? name : "error";
}
