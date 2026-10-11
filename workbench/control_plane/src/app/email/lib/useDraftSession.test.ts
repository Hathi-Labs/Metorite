// WS-17 EM-T17 PR-A2 — the composer half of "drafting never stalls"
// (`email_app_master_plan.md` §17.3.7 and §17.8 A5, A6 and the client half
// of A2d).
//
//   * A5: the composer aborts at `CLIENT_TOTAL_MS` and on Stop, keeps the text
//     of the member, and shows the message. An abort never falls back to the
//     JSON route `composeAssist`.
//   * A6: on a `model_*` or `server_timeout` error, the streamed preview stays.
//   * A2d (client half): `CLIENT_TOTAL_MS` is above the server bound.
//   * The `status` event shows as a step, and the `: keepalive` comment line
//     changes nothing.
//
// The suite runs in `environment: "node"`, so the run lives in the pure
// `runDraftRound` and the hook only wires React state to it.

import { afterEach, describe, expect, it, vi } from "vitest";

import { composeAssistStream, DraftStreamError, type ComposeAssistArgs } from "./api";
import {
  applyStatus,
  CLIENT_TOTAL_MS,
  DRAFT_NOTICES,
  runDraftRound,
  type DraftStep,
} from "./useDraftSession";

// The server bound of the whole run of one draft. It is `SERVER_TOTAL_S` in
// `apps/services/gateway/gateway/routes/email/automation/drafting.py` (PR-A).
// TypeScript cannot import a Python constant, so the number lives here, and
// `tests/unit/test_email_draft_no_stall.py` holds the Python side at 170.
const SERVER_TOTAL_S = 170;

const ARGS: ComposeAssistArgs = { accountId: "acc-1", mode: "reply", messageId: "m1", body: "My words." };
const STREAM_URL = "/api/email/compose-assist/stream";
const JSON_URL = "/api/email/compose-assist";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** A recording stand-in for the hook's state setters. */
function makeIO(controller = new AbortController()) {
  const io = {
    bodies: [] as string[],
    steps: [] as DraftStep[],
    thinking: "",
    notice: null as string | null,
    errors: [] as string[],
    controller,
  };
  return {
    io,
    wiring: {
      controller,
      onBody: (b: string) => io.bodies.push(b),
      setSteps: (fn: (prev: DraftStep[]) => DraftStep[]) => {
        io.steps = fn(io.steps);
      },
      setThinking: (fn: (prev: string) => string) => {
        io.thinking = fn(io.thinking);
      },
      setNotice: (n: string | null) => {
        io.notice = n;
      },
      onError: (m: string) => io.errors.push(m),
    },
  };
}

/** A fetch stub. The stream request hangs until its signal aborts, and the
 *  rejection is the abort reason, as a browser fetch does. */
function hangingFetch() {
  const calls: string[] = [];
  const fn = vi.fn((url: string, init?: RequestInit) => {
    calls.push(url);
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal;
      if (!signal) return;
      if (signal.aborted) reject(signal.reason);
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

/** A response whose SSE body is the given chunks, in order. */
function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const chunk of chunks) c.enqueue(enc.encode(chunk));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/** A response whose body sends the chunks and then hangs until the abort. */
function sseThenHang(chunks: string[], signal: AbortSignal): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const chunk of chunks) c.enqueue(enc.encode(chunk));
      signal.addEventListener("abort", () => c.error(signal.reason), { once: true });
    },
  });
  return new Response(body, { status: 200 });
}

const data = (obj: unknown) => `data: ${JSON.stringify(obj)}\n\n`;

describe("email-draft-client-bound (A2d client half)", () => {
  it("CLIENT_TOTAL_MS is 180000, above the server bound of 170 s", () => {
    expect(CLIENT_TOTAL_MS).toBe(180_000);
    expect(SERVER_TOTAL_S * 1000).toBeLessThan(CLIENT_TOTAL_MS);
  });
});

describe("email-draft-abort-no-fallback (A5)", () => {
  it("an abort while the stream opens calls no fallback", async () => {
    const calls = hangingFetch();
    const controller = new AbortController();
    const run = composeAssistStream(ARGS, { signal: controller.signal });
    controller.abort();
    await expect(run).rejects.toBeDefined();
    expect(calls).toEqual([STREAM_URL]);
  });

  it("an abort while the stream reads calls no fallback", async () => {
    const controller = new AbortController();
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return sseThenHang([data({ type: "delta", kind: "content", text: "Hel" })], controller.signal);
      }),
    );
    const deltas: string[] = [];
    const run = composeAssistStream(ARGS, {
      signal: controller.signal,
      onDelta: (_k, t) => deltas.push(t),
    });
    await vi.waitFor(() => expect(deltas).toEqual(["Hel"]));
    controller.abort();
    await expect(run).rejects.toBeDefined();
    expect(calls).toEqual([STREAM_URL]);
  });

  it("a stream that cannot open for another cause still falls back", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        if (url === STREAM_URL) return new Response(null, { status: 404 });
        return new Response(JSON.stringify({ draft: "From JSON" }), { status: 200 });
      }),
    );
    await expect(composeAssistStream(ARGS)).resolves.toEqual({ draft: "From JSON" });
    expect(calls).toEqual([STREAM_URL, JSON_URL]);
  });

  it("Stop keeps the text of the member, says so, and calls no fallback", async () => {
    const calls = hangingFetch();
    const { io, wiring } = makeIO();
    const run = runDraftRound(ARGS, { instruction: "" }, wiring);
    wiring.controller.abort();
    await expect(run).resolves.toBeNull();
    expect(calls).toEqual([STREAM_URL]);
    expect(io.bodies.at(-1)).toBe("My words.");
    expect(io.notice).toBe(DRAFT_NOTICES.stopped);
    expect(io.errors).toEqual([]);
  });
});

describe("email-draft-client-timeout (A5)", () => {
  it("the composer aborts at 180 s, keeps the text of the member and shows the message", async () => {
    vi.useFakeTimers();
    const calls = hangingFetch();
    const { io, wiring } = makeIO();
    const run = runDraftRound({ ...ARGS }, { instruction: "", quote: "> quoted" }, wiring);

    await vi.advanceTimersByTimeAsync(CLIENT_TOTAL_MS - 1);
    expect(wiring.controller.signal.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    expect(wiring.controller.signal.aborted).toBe(true);
    await expect(run).resolves.toBeNull();

    expect(calls).toEqual([STREAM_URL]);
    expect(io.bodies.at(-1)).toBe("My words.\n\n> quoted");
    expect(io.notice).toBe("The draft took too long. Try again.");
    expect(io.notice).toBe(DRAFT_NOTICES.timeout);
  });

  it("a draft that lands in time clears the timer", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async () => sseResponse([data({ type: "done", draft: "Hello" })])));
    const { io, wiring } = makeIO();
    await expect(runDraftRound(ARGS, { instruction: "" }, wiring)).resolves.toBe("Hello");
    await vi.advanceTimersByTimeAsync(CLIENT_TOTAL_MS * 2);
    expect(wiring.controller.signal.aborted).toBe(false);
    expect(io.notice).toBeNull();
  });
});

describe("email-draft-preview-stays (A6)", () => {
  const REASONS = ["model_slow", "model_stalled", "model_timeout", "server_timeout"] as const;

  it.each(REASONS)("on %s the streamed preview stays in the composer", async (reason) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          data({ type: "delta", kind: "content", text: "Hello Ravi, " }),
          data({ type: "delta", kind: "content", text: "the quote is" }),
          data({ type: "error", error: "the model stopped", reason }),
        ]),
      ),
    );
    const { io, wiring } = makeIO();
    await expect(runDraftRound(ARGS, { instruction: "" }, wiring)).resolves.toBeNull();
    expect(io.bodies.at(-1)).toBe("Hello Ravi, the quote is");
    expect(io.notice).toBe(DRAFT_NOTICES[reason]);
    expect(io.errors).toEqual([]);
  });

  it("a typed error with no preview gives the text of the member back", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse([data({ type: "error", error: "slow", reason: "model_slow" })])),
    );
    const { io, wiring } = makeIO();
    await runDraftRound(ARGS, { instruction: "" }, wiring);
    expect(io.bodies.at(-1)).toBe("My words.");
    expect(io.notice).toBe(DRAFT_NOTICES.model_slow);
  });

  it("an error with no typed reason keeps the old behaviour: the text of the member, and onError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          data({ type: "delta", kind: "content", text: "Half" }),
          data({ type: "error", error: "draft failed" }),
        ]),
      ),
    );
    const { io, wiring } = makeIO();
    await runDraftRound(ARGS, { instruction: "" }, wiring);
    expect(io.bodies.at(-1)).toBe("My words.");
    expect(io.errors).toEqual(["draft failed"]);
    expect(io.notice).toBeNull();
  });

  it("the stream gives the typed reason to the caller", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse([data({ type: "error", error: "x", reason: "model_stalled" })])),
    );
    const err = await composeAssistStream(ARGS).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DraftStreamError);
    expect((err as DraftStreamError).reason).toBe("model_stalled");
  });
});

describe("email-draft-status-step", () => {
  it("a status event shows as a step, and a keepalive line changes nothing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          data({ type: "status", stage: "context" }),
          ": keepalive\n\n",
          ": keepalive\n\n",
          data({ type: "done", draft: "Hello" }),
        ]),
      ),
    );
    const { io, wiring } = makeIO();
    await expect(runDraftRound(ARGS, { instruction: "" }, wiring)).resolves.toBe("Hello");
    expect(io.steps.map((s) => [s.key, s.label])).toEqual([["stage:context", "Thread context"]]);
  });

  it("a keepalive line split across two reads changes nothing", async () => {
    const statuses: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([": keep", "alive\n\n", data({ type: "status", stage: "draft" }), data({ type: "done", draft: "A" })]),
      ),
    );
    await expect(
      composeAssistStream(ARGS, { onStatus: (stage) => statuses.push(stage) }),
    ).resolves.toEqual({ draft: "A", skipped: undefined });
    expect(statuses).toEqual(["draft"]);
  });

  it("a status adds a running step and never moves a done step back", () => {
    let steps = applyStatus([], "context");
    expect(steps).toEqual([{ key: "stage:context", label: "Thread context", detail: "", state: "running" }]);
    steps = steps.map((s) => ({ ...s, state: "done" as const }));
    expect(applyStatus(steps, "context")).toEqual(steps);
    expect(applyStatus(steps, "fetch").map((s) => s.label)).toEqual(["Thread context", "fetch"]);
  });

  it("a step that still runs when the draft stops shows as failed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_u: string, init?: RequestInit) =>
        sseThenHang([data({ type: "status", stage: "context" })], init!.signal!),
      ),
    );
    const { io, wiring } = makeIO();
    const run = runDraftRound(ARGS, { instruction: "" }, wiring);
    await vi.waitFor(() => expect(io.steps).toHaveLength(1));
    wiring.controller.abort();
    await run;
    expect(io.steps.map((s) => s.state)).toEqual(["failed"]);
  });
});
