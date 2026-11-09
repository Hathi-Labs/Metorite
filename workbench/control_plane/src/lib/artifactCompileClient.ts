/**
 * The browser half of the React artifact compile (task 2432 fix round).
 *
 * `POST /api/artifacts/compile` can now refuse for a while, for reasons that
 * are not the source's fault:
 *   - 429 `busy`: the one build slot of the server is taken and its queue is
 *     full;
 *   - 429 `member_busy`: another build of this member is still running;
 *   - 503 (or no answer at all): the server could not take the request.
 * Before this module, each of these was a final "failed to build", so a chat
 * with many new React cards ended with sticky errors (live: 16 cards at once
 * gave 9 "busy").
 *
 * So this module:
 *   1. sends ONE compile at a time from this tab ({@link serialQueue}). A
 *      chat with 16 new cards sends 16 compiles one after the other, and the
 *      server never sees two from this tab at once;
 *   2. retries a passing refusal after its `Retry-After`, with jitter, up to
 *      {@link MAX_RETRIES} times or {@link WAIT_BUDGET_MS} of waiting. The card keeps "Building…" meanwhile;
 *   3. stops after that with `unavailable`, which the card shows as "not
 *      built yet" with a "Try again" control, never as "failed to build";
 *   4. never retries 429 `rate_limited` by itself. The card shows the
 *      member's limit sentence.
 */

/** Retries of a passing refusal before the card stops and offers "Try again". */
export const MAX_RETRIES = 8;

/**
 * The card keeps retrying until it has waited this long in total. It must
 * exceed one killed build (`COMPILE_LIMITS.timeoutMs` in compileArtifact.ts,
 * which a browser module cannot import: it pulls in esbuild). Then a second
 * tab of the same member outlasts the slow build of the first tab. A test
 * holds this above `timeoutMs`.
 */
export const WAIT_BUDGET_MS = 14_000;

/** The longest wait the client takes from a `Retry-After`. */
export const MAX_RETRY_AFTER_MS = 30_000;

/** The wait when a passing refusal names none. */
export const DEFAULT_RETRY_AFTER_MS = 2_000;

/** Random extra wait, so cards that were refused together do not return together. */
export const JITTER_MS = 750;

export const COULD_NOT_REACH =
  "Metorite could not reach the artifact builder. Check your connection and try again.";
export const NOT_BUILT_YET =
  "Metorite is busy building other artifacts, so this one is not built yet. Try again in a moment.";

/** What the card does with one answer of the route. */
export type CompileAnswer =
  | { kind: "ok"; js: string }
  /** The source's fault: final. The card says it failed to build. */
  | { kind: "error"; error: string }
  /** A passing refusal: wait `retryAfterMs`, then send it again. */
  | { kind: "retry"; error: string; retryAfterMs: number }
  /** The member's rate limit: final until the member asks again. */
  | { kind: "limited"; error: string };

/** The end of one compile, after any retries. */
export type CompileOutcome =
  | { kind: "ok"; js: string }
  | { kind: "error"; error: string }
  | { kind: "limited"; error: string }
  /** The passing refusals did not pass in {@link MAX_RETRIES} retries. */
  | { kind: "unavailable"; error: string };

/** `Retry-After` in seconds, as milliseconds, bounded. */
export function retryAfterMs(header: string | null): number {
  const seconds = header !== null && /^\d+$/.test(header.trim()) ? Number(header.trim()) : NaN;
  if (!Number.isFinite(seconds)) return DEFAULT_RETRY_AFTER_MS;
  return Math.min(Math.max(seconds * 1000, 250), MAX_RETRY_AFTER_MS);
}

/** Map one answer of `POST /api/artifacts/compile` to what the card does. */
export function classifyCompileAnswer(
  status: number,
  body: unknown,
  retryAfter: string | null,
): CompileAnswer {
  const b = (body && typeof body === "object" ? body : {}) as {
    ok?: unknown;
    js?: unknown;
    error?: unknown;
    code?: unknown;
  };
  const error = typeof b.error === "string" && b.error ? b.error : null;
  if (status === 200 && b.ok === true && typeof b.js === "string") return { kind: "ok", js: b.js };
  if (status === 429 && b.code === "rate_limited") {
    return { kind: "limited", error: error ?? "You built many artifacts in a short time. Wait a few minutes and try again." };
  }
  if (status === 429 || status === 503 || status === 502 || status === 504) {
    return { kind: "retry", error: error ?? NOT_BUILT_YET, retryAfterMs: retryAfterMs(retryAfter) };
  }
  return { kind: "error", error: error ?? `The artifact builder answered ${status}. Try again.` };
}

/** What {@link compileWithRetry} needs from the outside. Tests pass fakes. */
export interface CompileDeps {
  post: (code: string) => Promise<{ status: number; body: unknown; retryAfter: string | null }>;
  sleep: (ms: number) => Promise<void>;
  random: () => number;
}

/** One compile, with the retries of a passing refusal. */
export async function compileWithRetry(code: string, deps: CompileDeps): Promise<CompileOutcome> {
  let waited = 0;
  for (let attempt = 0; ; attempt += 1) {
    let answer: CompileAnswer;
    try {
      const res = await deps.post(code);
      answer = classifyCompileAnswer(res.status, res.body, res.retryAfter);
    } catch {
      // No answer at all: the request may never have arrived. A compile has
      // no side effect, so sending it again is safe.
      answer = { kind: "retry", error: COULD_NOT_REACH, retryAfterMs: DEFAULT_RETRY_AFTER_MS };
    }
    if (answer.kind !== "retry") return answer;
    if (attempt >= MAX_RETRIES || waited >= WAIT_BUDGET_MS) {
      return { kind: "unavailable", error: answer.error };
    }
    const wait = answer.retryAfterMs + Math.floor(deps.random() * JITTER_MS);
    waited += wait;
    await deps.sleep(wait);
  }
}

/** Run tasks one at a time, in order. A failed task does not stop the next. */
export function serialQueue(): <T>(task: () => Promise<T>) => Promise<T> {
  let tail: Promise<unknown> = Promise.resolve();
  return <T>(task: () => Promise<T>): Promise<T> => {
    const run = tail.then(task, task);
    tail = run.catch(() => undefined);
    return run;
  };
}

// ── The tab's own compiler: one queue, one cache ───────────────────────────

const inOrder = serialQueue();
/** Built bundles, keyed by source, shared across mounts and re-renders. */
const bundles = new Map<string, string>();
/** Compiles in flight, so N cards of the same source send one request. */
const inFlight = new Map<string, Promise<CompileOutcome>>();

const browserDeps: CompileDeps = {
  post: async (code) => {
    const res = await fetch("/api/artifacts/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const body: unknown = await res.json().catch(() => null);
    return { status: res.status, body, retryAfter: res.headers.get("retry-after") };
  },
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  random: () => Math.random(),
};

/** The built bundle of `code`, if this tab built it already. */
export function cachedBundle(code: string): string | undefined {
  return bundles.get(code);
}

/**
 * Build `code` in this tab's queue, with the retries. A built bundle is kept,
 * so a re-render never sends it again. `deps` is for tests only.
 */
export function requestCompile(code: string, deps: CompileDeps = browserDeps): Promise<CompileOutcome> {
  const built = bundles.get(code);
  if (built !== undefined) return Promise.resolve({ kind: "ok", js: built });
  const running = inFlight.get(code);
  if (running) return running;
  const request = inOrder(() => compileWithRetry(code, deps))
    .then((outcome) => {
      if (outcome.kind === "ok") bundles.set(code, outcome.js);
      return outcome;
    })
    .finally(() => {
      inFlight.delete(code);
    });
  inFlight.set(code, request);
  return request;
}
