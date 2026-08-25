/**
 * testRunner — Phase 2b: the ephemeral-store test runner for Custom Apps
 * (docs/app-workshop/README.md §4.9).
 *
 * The load-bearing design decision: a scenario never touches the real
 * backend. `runScenario()` builds a throwaway bundle, launches its OWN
 * hidden, offscreen `<iframe>`, and injects the exact same `window.cc` /
 * `window.__ccTest` SDK as the visible preview (`buildAppSrcDoc()`, reused
 * verbatim from ccBridge.ts) — so a scenario exercises the real capability
 * surface an app author sees. What's different from the live bridge is
 * entirely on THIS side of the postMessage channel: every `cc.*` call the
 * app makes is answered synchronously against an in-memory store seeded
 * from `scenario.seed`, instead of being brokered to
 * `/api/apps/{slug}/…`. No real outward write, no real AI spend, no real
 * `app_data` row is ever touched — which is what makes it safe to run
 * automatically and often (every builder turn), not just when someone
 * remembers to click "test."
 *
 * This is its OWN small broker, deliberately not a reuse of `useCcBridge` —
 * that hook is a React effect wired to the live DRAFT preview iframe and
 * the real API; bolting a "test mode" flag onto it would blur two things
 * that need to stay separate (see §4.9, "deliberately out of v1 scope").
 * Nothing here is a React hook — plain async functions, so a scenario can
 * be run from anywhere (chat-triggered auto-run, a "Run tests" button, a
 * CI-style check), not just from a component render.
 */

import { buildAppSrcDoc } from "./ccBridge";

// ─── Scenario / result types (shared contract — do not rename or reshape) ─

export type TestStepAction =
  | { action: "click"; selector: string }
  | { action: "type"; selector: string; text: string }
  | { action: "select"; selector: string; value: string }
  | { action: "wait"; ms: number };

export type TestAssertionOp =
  | "eq"
  | "neq"
  | "lt"
  | "lte"
  | "gt"
  | "gte"
  | "contains"
  | "exists"
  | "not-exists";

export type TestAssertion =
  | {
      kind: "storage";
      table: string;
      key: string;
      path?: string;
      op: TestAssertionOp;
      value?: unknown;
    }
  | { kind: "dom-text"; selector: string; op: "eq" | "contains"; value: string }
  | { kind: "dom-exists"; selector: string; expect: boolean };

export interface TestScenarioSeed {
  storage?: Record<string, Record<string, unknown>>; // tableName -> { key: value }
  toolResponses?: Record<string, unknown>; // tool name -> canned result payload
  aiResponse?: string; // canned text for any cc.ai.complete call
}

export interface TestScenario {
  id: string;
  name: string;
  seed?: TestScenarioSeed;
  steps: TestStepAction[];
  assertions: TestAssertion[];
}

export interface TestStepResult {
  step: TestStepAction;
  ok: boolean;
  error?: string;
}

export interface TestAssertionResult {
  assertion: TestAssertion;
  passed: boolean;
  actual?: unknown;
  error?: string;
}

export interface TestResult {
  scenarioId: string;
  passed: boolean;
  steps: TestStepResult[];
  assertions: TestAssertionResult[];
  consoleErrors: string[];
  durationMs: number;
  error?: string; // set on a fatal/setup failure (iframe never loaded, step aborted, timeout)
}

// ─── Ephemeral store ────────────────────────────────────────────────────

/** The throwaway `cc` backing store for one scenario run — never persisted. */
export interface EphemeralStore {
  storage: Record<string, Record<string, unknown>>;
  toolResponses: Record<string, unknown>;
  aiResponse?: string;
}

/** Deep-clone the seed so step mutations never leak back into the caller's
 * scenario object — a scenario may be reused across many runs. */
function buildStore(seed?: TestScenarioSeed): EphemeralStore {
  const cloned: TestScenarioSeed = seed
    ? (JSON.parse(JSON.stringify(seed)) as TestScenarioSeed)
    : {};
  return {
    storage: cloned.storage ?? {},
    toolResponses: cloned.toolResponses ?? {},
    aiResponse: cloned.aiResponse,
  };
}

// ─── Assertion helpers (pure, exported for isolated review) ─────────────

/** Resolve a simple dot-path ("a.b.c") against a value. No arrays/wildcards
 * — deliberately not a JSONPath engine, the scenario vocabulary stays small
 * (RFC §4.9). Undefined at any hop resolves to `undefined`, not a throw. */
export function resolveDotPath(value: unknown, path?: string): unknown {
  if (!path) return value;
  let cur: unknown = value;
  for (const segment of path.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[segment];
  }
  return cur;
}

function compareOp(op: TestAssertionOp, actual: unknown, expected: unknown): boolean {
  switch (op) {
    case "eq":
      return JSON.stringify(actual) === JSON.stringify(expected);
    case "neq":
      return JSON.stringify(actual) !== JSON.stringify(expected);
    case "lt":
      return typeof actual === "number" && typeof expected === "number" && actual < expected;
    case "lte":
      return typeof actual === "number" && typeof expected === "number" && actual <= expected;
    case "gt":
      return typeof actual === "number" && typeof expected === "number" && actual > expected;
    case "gte":
      return typeof actual === "number" && typeof expected === "number" && actual >= expected;
    case "contains":
      if (typeof actual === "string") return actual.includes(String(expected));
      if (Array.isArray(actual)) return actual.includes(expected);
      return false;
    case "exists":
      return actual !== undefined;
    case "not-exists":
      return actual === undefined;
    default:
      return false;
  }
}

/** Pure: evaluate a `kind: "storage"` assertion directly against the
 * ephemeral store (no RPC needed — the runner owns the object). */
export function evaluateStorageAssertion(
  store: EphemeralStore,
  assertion: Extract<TestAssertion, { kind: "storage" }>
): { passed: boolean; actual: unknown } {
  const row: unknown = store.storage[assertion.table]?.[assertion.key];
  const actual = resolveDotPath(row, assertion.path);
  return { passed: compareOp(assertion.op, actual, assertion.value), actual };
}

/** Pure: evaluate a `kind: "dom-text"` assertion given text already read
 * from the frame via `test.text`. */
export function evaluateDomTextAssertion(
  assertion: Extract<TestAssertion, { kind: "dom-text" }>,
  actual: string | null
): { passed: boolean } {
  if (assertion.op === "eq") return { passed: actual === assertion.value };
  return { passed: typeof actual === "string" && actual.includes(assertion.value) };
}

/** Pure: evaluate a `kind: "dom-exists"` assertion given the flag already
 * read from the frame via `test.exists`. */
export function evaluateDomExistsAssertion(
  assertion: Extract<TestAssertion, { kind: "dom-exists" }>,
  actualExists: boolean
): { passed: boolean } {
  return { passed: actualExists === assertion.expect };
}

// ─── cc.* dispatch against the ephemeral store ───────────────────────────

/**
 * Answers one `cc.*` RPC request the app frame made, synchronously, against
 * the ephemeral store — the test-mode mirror of ccBridge.ts's `dispatch()`.
 * Shapes intentionally match the real dispatch (e.g. `storage.list`'s
 * `{key,value}` rows) so a scenario author's mental model matches
 * production.
 */
function dispatchCc(
  store: EphemeralStore,
  method: string,
  args: unknown[]
): { result?: unknown } | { error: string } {
  const [a0, a1, a2] = args;
  switch (method) {
    case "user.me":
      return { result: { email: "test@fracktal.in", role: "employee" } };
    case "storage.list": {
      const table = typeof a0 === "string" ? a0 : "";
      const rows = Object.entries(store.storage[table] ?? {}).map(([key, value]) => ({
        key,
        value,
      }));
      return { result: rows };
    }
    case "storage.get": {
      const table = typeof a0 === "string" ? a0 : "";
      const key = typeof a1 === "string" ? a1 : "";
      return { result: store.storage[table]?.[key] ?? null };
    }
    case "storage.set": {
      const table = typeof a0 === "string" ? a0 : "";
      const key = typeof a1 === "string" ? a1 : "";
      if (!store.storage[table]) store.storage[table] = {};
      store.storage[table][key] = a2;
      return { result: true };
    }
    case "storage.delete": {
      const table = typeof a0 === "string" ? a0 : "";
      const key = typeof a1 === "string" ? a1 : "";
      if (store.storage[table]) delete store.storage[table][key];
      return { result: true };
    }
    case "tools.call": {
      // No confirm handshake at all in test mode — nothing real happens, so
      // there is nothing to gate on a human decision.
      const tool = typeof a0 === "string" ? a0 : "";
      return { result: store.toolResponses[tool] ?? { ok: true } };
    }
    case "ai.complete": {
      return {
        result: {
          text: store.aiResponse ?? "(test mode — no AI response configured)",
          model: "test",
          usage: { tokens_in: 0, tokens_out: 0 },
        },
      };
    }
    default:
      return { error: "unsupported in test mode: " + method };
  }
}

// ─── Runner ───────────────────────────────────────────────────────────────

/** Whole-run safety valve if `opts.timeoutMs` isn't given. */
const DEFAULT_TIMEOUT_MS = 10_000;
/** Per-step RPC timeout ceiling (click/type/select/dom read). */
const DEFAULT_STEP_TIMEOUT_MS = 5_000;
/** Hard cap on a `wait` step regardless of what the scenario requests —
 * a safety valve against a runaway scenario. */
const MAX_WAIT_MS = 2_000;
/** Per-attempt timeout inside the retry loops below — generous for a
 * synchronous DOM query + postMessage round trip, short enough that several
 * attempts fit inside one step's overall budget. */
const RETRY_ATTEMPT_TIMEOUT_MS = 300;
/** Delay between retry attempts. */
const RETRY_INTERVAL_MS = 100;

type SendTestRequest = (method: string, args: unknown[], timeoutMs: number) => Promise<unknown>;

/** Retry an operation that REJECTS while its target element isn't in the DOM
 * yet (`click`/`type`/`select` throw via ccBridge.ts's `requireEl`) until
 * `overallTimeoutMs` wall-clock passes, instead of failing on the first
 * miss. React's idiomatic async data-loading pattern (`useEffect` → a
 * `cc.storage` round trip → `setState`) commits one render tick after the
 * frame's `load` event, so a step's target may not exist yet even though
 * the frame itself is ready — this absorbs that race. The same race exists
 * for a T1 app that seeds itself asynchronously, so this benefits both. */
async function retryUntilResolved<T>(
  attempt: () => Promise<T>,
  overallTimeoutMs: number
): Promise<T> {
  const deadline = Date.now() + overallTimeoutMs;
  let lastError: unknown;
  for (;;) {
    try {
      return await attempt();
    } catch (e) {
      lastError = e;
      const remaining = deadline - Date.now();
      if (remaining <= 0) break;
      await new Promise((resolve) => setTimeout(resolve, Math.min(RETRY_INTERVAL_MS, remaining)));
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError ?? "unknown error"));
}

/** Poll an operation that RESOLVES but may report "not found yet" — ccBridge
 * `text`/`exists` never throw on a missing element, they just resolve
 * `null`/`false`, so the reject-and-retry approach above doesn't apply to
 * them. Retries while `notFoundYet(result)` holds, same deadline/interval
 * shape as `retryUntilResolved`. */
async function pollUntilFound<T>(
  attempt: () => Promise<T>,
  notFoundYet: (result: T) => boolean,
  overallTimeoutMs: number
): Promise<T> {
  const deadline = Date.now() + overallTimeoutMs;
  for (;;) {
    const result = await attempt();
    const remaining = deadline - Date.now();
    if (!notFoundYet(result) || remaining <= 0) return result;
    await new Promise((resolve) => setTimeout(resolve, Math.min(RETRY_INTERVAL_MS, remaining)));
  }
}

async function runStep(step: TestStepAction, send: SendTestRequest, timeoutMs: number): Promise<void> {
  const attemptTimeoutMs = Math.min(RETRY_ATTEMPT_TIMEOUT_MS, timeoutMs);
  switch (step.action) {
    case "click":
      await retryUntilResolved(() => send("test.click", [step.selector], attemptTimeoutMs), timeoutMs);
      return;
    case "type":
      await retryUntilResolved(
        () => send("test.type", [step.selector, step.text], attemptTimeoutMs),
        timeoutMs
      );
      return;
    case "select":
      await retryUntilResolved(
        () => send("test.select", [step.selector, step.value], attemptTimeoutMs),
        timeoutMs
      );
      return;
    case "wait":
      await new Promise<void>((resolve) => {
        setTimeout(resolve, Math.min(step.ms, MAX_WAIT_MS));
      });
      return;
  }
}

async function evaluateAssertion(
  store: EphemeralStore,
  assertion: TestAssertion,
  send: SendTestRequest,
  timeoutMs: number
): Promise<TestAssertionResult> {
  try {
    if (assertion.kind === "storage") {
      const { passed, actual } = evaluateStorageAssertion(store, assertion);
      return { assertion, passed, actual };
    }
    const attemptTimeoutMs = Math.min(RETRY_ATTEMPT_TIMEOUT_MS, timeoutMs);
    if (assertion.kind === "dom-text") {
      const actual = (await pollUntilFound(
        () => send("test.text", [assertion.selector], attemptTimeoutMs),
        (r) => r === null,
        timeoutMs
      )) as string | null;
      const { passed } = evaluateDomTextAssertion(assertion, actual);
      return { assertion, passed, actual };
    }
    const expectPresent = assertion.expect;
    const actual = (await pollUntilFound(
      () => send("test.exists", [assertion.selector], attemptTimeoutMs),
      (r) => r === false && expectPresent === true,
      timeoutMs
    )) as boolean;
    const { passed } = evaluateDomExistsAssertion(assertion, actual);
    return { assertion, passed, actual };
  } catch (e) {
    return { assertion, passed: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/**
 * Run one scenario against `bundleHtml` inside a fresh, hidden, offscreen
 * iframe. Ephemeral end to end: a new store, a new frame, torn down when
 * the run finishes (success, failure, or timeout — see the `finally` below).
 */
export async function runScenario(
  bundleHtml: string,
  scenario: TestScenario,
  opts?: { slug?: string; timeoutMs?: number }
): Promise<TestResult> {
  const startedAt = Date.now();
  const overallTimeoutMs = opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  // Never wait longer for one step than we would for the whole run.
  const stepTimeoutMs = Math.min(DEFAULT_STEP_TIMEOUT_MS, overallTimeoutMs);

  const store = buildStore(scenario.seed);
  const consoleErrors: string[] = [];
  const stepResults: TestStepResult[] = [];

  const iframe = document.createElement("iframe");
  iframe.setAttribute("sandbox", "allow-scripts");
  // Offscreen but genuinely rendered — NOT display:none, which can suspend
  // layout/script execution for an iframe in some browsers.
  iframe.style.cssText =
    "position: fixed; left: -99999px; top: 0; width: 1024px; height: 768px; border: 0;";

  const pending = new Map<
    string,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  let seq = 0;

  function sendTestRequest(method: string, args: unknown[], timeoutMs: number): Promise<unknown> {
    return new Promise((resolve, reject) => {
      seq += 1;
      const id = "test-" + seq + "-" + Date.now();
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(method + " timed out after " + timeoutMs + "ms"));
      }, timeoutMs);
      pending.set(id, {
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v);
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      });
      const win = iframe.contentWindow;
      if (!win) {
        clearTimeout(timer);
        pending.delete(id);
        reject(new Error("test iframe is not ready"));
        return;
      }
      win.postMessage({ __ccsdk: true, id, method, args }, "*");
    });
  }

  function onMessage(ev: MessageEvent) {
    if (ev.source !== iframe.contentWindow) return;
    const data: unknown = ev.data;
    if (!data || typeof data !== "object") return;
    const msg = data as Record<string, unknown>;
    if (msg.__ccsdk !== true) return;

    // Fire-and-forget console notification (no id) — same shape as the
    // visible bridge's onConsoleEvent; only "error" level matters here,
    // matching what the console drawer treats as an error.
    if (msg.event === "console") {
      if (msg.level === "error") {
        const message = typeof msg.message === "string" ? msg.message : String(msg.message);
        const stack = typeof msg.stack === "string" ? msg.stack : undefined;
        consoleErrors.push(stack ? message + "\n" + stack : message);
      }
      return;
    }

    if (typeof msg.id !== "string") return;

    // A message carrying `method` is an incoming cc.* RPC REQUEST from the
    // app frame (dispatched against the ephemeral store below); one with no
    // `method` is a REPLY to one of our own test.* requests — the same
    // disambiguation the in-frame SDK itself uses for its two message
    // sources.
    if (typeof msg.method === "string") {
      const args = Array.isArray(msg.args) ? msg.args : [];
      const outcome = dispatchCc(store, msg.method, args);
      const win = iframe.contentWindow;
      if (!win) return;
      // targetOrigin "*": the frame is a sandboxed srcDoc without
      // allow-same-origin, so it has an opaque ("null") origin that cannot
      // be named as a targetOrigin — same justification as ccBridge.ts's
      // parent-side broker. `win` is exactly the frame we created above.
      if ("error" in outcome) {
        win.postMessage({ __ccsdk: true, id: msg.id, error: outcome.error }, "*");
      } else {
        win.postMessage({ __ccsdk: true, id: msg.id, result: outcome.result ?? null }, "*");
      }
      return;
    }

    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    if (msg.error != null) p.reject(new Error(String(msg.error)));
    else p.resolve(msg.result);
  }

  try {
    // Listener + onload BEFORE content, per the load-order this iframe
    // relies on: srcdoc's synchronous scripts (which install window.cc /
    // window.__ccTest and may start posting) must never race an
    // unregistered listener.
    window.addEventListener("message", onMessage);
    const ready = new Promise<void>((resolve) => {
      iframe.onload = () => resolve();
    });
    document.body.appendChild(iframe);
    iframe.srcdoc = buildAppSrcDoc(bundleHtml, {
      slug: opts?.slug ?? "test",
      mode: "draft",
    });

    const run = (async (): Promise<TestResult> => {
      // The native `load` event — fires once the srcdoc document's
      // synchronous scripts (including the injected SDK) have run. No
      // custom ready-ping protocol; the existing SDK doesn't have one and
      // doesn't need one for this.
      await ready;

      for (const step of scenario.steps) {
        try {
          await runStep(step, sendTestRequest, stepTimeoutMs);
          stepResults.push({ step, ok: true });
        } catch (e) {
          const message = e instanceof Error ? e.message : String(e);
          stepResults.push({ step, ok: false, error: message });
          // First step failure stops the run: no further steps, no
          // assertions.
          return {
            scenarioId: scenario.id,
            passed: false,
            steps: stepResults,
            assertions: [],
            consoleErrors,
            durationMs: Date.now() - startedAt,
            error: `step ${stepResults.length} (${step.action}) failed: ${message}`,
          };
        }
      }

      const assertionResults: TestAssertionResult[] = [];
      for (const assertion of scenario.assertions) {
        assertionResults.push(
          await evaluateAssertion(store, assertion, sendTestRequest, stepTimeoutMs)
        );
      }

      return {
        scenarioId: scenario.id,
        passed:
          stepResults.every((s) => s.ok) && assertionResults.every((a) => a.passed),
        steps: stepResults,
        assertions: assertionResults,
        consoleErrors,
        durationMs: Date.now() - startedAt,
      };
    })();

    const timeout = new Promise<TestResult>((resolve) => {
      setTimeout(() => {
        resolve({
          scenarioId: scenario.id,
          passed: false,
          steps: stepResults,
          assertions: [],
          consoleErrors,
          durationMs: Date.now() - startedAt,
          error: "timed out",
        });
      }, overallTimeoutMs);
    });

    return await Promise.race([run, timeout]);
  } finally {
    // Always: success, failure, or timeout.
    window.removeEventListener("message", onMessage);
    iframe.remove();
  }
}

/**
 * Run every scenario sequentially — one hidden iframe alive at a time, not
 * N in parallel (deliberate: this donates the viewer's own tab compute,
 * same "compute donated by the viewer" property as the rest of the runtime,
 * RFC §7.6 — running scenarios concurrently would multiply that cost for no
 * benefit a non-technical owner would notice).
 */
export async function runAllScenarios(
  bundleHtml: string,
  scenarios: TestScenario[],
  opts?: { slug?: string; timeoutMs?: number }
): Promise<TestResult[]> {
  const results: TestResult[] = [];
  for (const scenario of scenarios) {
    results.push(await runScenario(bundleHtml, scenario, opts));
  }
  return results;
}
