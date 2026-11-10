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

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { composeAssistStream, DraftStreamError, type ComposeAssistArgs } from "./api";
import {
  applyStatus,
  CLIENT_TOTAL_MS,
  createDraftRounds,
  DRAFT_NOTICES,
  runDraftRound,
  STOP_GRACE_MS,
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
    // The round wrote nothing, so it writes nothing back.
    expect(io.bodies).toEqual([]);
    expect(io.notice).toBe(DRAFT_NOTICES.stopped);
    expect(io.errors).toEqual([]);
  });
});

describe("email-draft-client-timeout (A5)", () => {
  it("the composer aborts at 180 s, keeps the text of the member and shows the message", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(url);
        return sseThenHang([data({ type: "delta", kind: "content", text: "Half" })], init!.signal!);
      }),
    );
    const { io, wiring } = makeIO();
    const run = runDraftRound({ ...ARGS }, { instruction: "", quote: "> quoted" }, wiring);

    await vi.advanceTimersByTimeAsync(CLIENT_TOTAL_MS - 1);
    expect(wiring.controller.signal.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    expect(wiring.controller.signal.aborted).toBe(true);
    await expect(run).resolves.toBeNull();

    expect(calls).toEqual([STREAM_URL]);
    // The round wrote a preview, so it gives back the text of the member.
    expect(io.bodies).toEqual(["Half\n\n> quoted", "My words.\n\n> quoted"]);
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

  it("a typed error with no preview leaves the body alone", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse([data({ type: "error", error: "slow", reason: "model_slow" })])),
    );
    const { io, wiring } = makeIO();
    await runDraftRound(ARGS, { instruction: "" }, wiring);
    expect(io.bodies).toEqual([]);
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

/** A stream the test feeds one chunk at a time. It errors on the abort. */
function pushStream() {
  const enc = new TextEncoder();
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  return {
    fetch: vi.fn(async (_u: string, init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => ctl.error(init.signal!.reason), { once: true });
      return new Response(body, { status: 200 });
    }),
    push: (chunk: string) => ctl.enqueue(enc.encode(chunk)),
    close: () => ctl.close(),
  };
}

/** A composer body that the round and the member both write. */
function makeComposer(start: string) {
  const composer = { body: start };
  const { io, wiring } = makeIO();
  const onBody = (b: string) => {
    io.bodies.push(b);
    composer.body = b;
  };
  return { composer, io, wiring: { ...wiring, onBody } };
}

describe("email-draft-member-text-stays (fix round 1, P1 #1)", () => {
  it("the member types during a stalled round, presses Stop, and the typed text stays", async () => {
    hangingFetch();
    const { composer, io, wiring } = makeComposer("My words.");
    const run = runDraftRound(ARGS, { instruction: "" }, wiring);
    composer.body = "My words. And more that I typed.";
    wiring.controller.abort();
    await run;
    expect(composer.body).toBe("My words. And more that I typed.");
    expect(io.bodies).toEqual([]);
    expect(io.notice).toBe(DRAFT_NOTICES.stopped);
  });

  it("the same at the client bound", async () => {
    vi.useFakeTimers();
    hangingFetch();
    const { composer, io, wiring } = makeComposer("My words.");
    const run = runDraftRound(ARGS, { instruction: "" }, wiring);
    composer.body = "My words. Typed meanwhile.";
    await vi.advanceTimersByTimeAsync(CLIENT_TOTAL_MS);
    await run;
    expect(composer.body).toBe("My words. Typed meanwhile.");
    expect(io.notice).toBe(DRAFT_NOTICES.timeout);
  });

  it("the same on model_slow", async () => {
    const s = pushStream();
    vi.stubGlobal("fetch", s.fetch);
    const { composer, io, wiring } = makeComposer("My words.");
    const run = runDraftRound(ARGS, { instruction: "" }, wiring);
    composer.body = "My words. Typed meanwhile.";
    s.push(data({ type: "error", error: "slow", reason: "model_slow" }));
    s.close();
    await run;
    expect(composer.body).toBe("My words. Typed meanwhile.");
    expect(io.notice).toBe(DRAFT_NOTICES.model_slow);
  });
});

describe("email-draft-reset-ends-the-round (fix round 1, P1 #2)", () => {
  it("a reset aborts the round, and the round writes nothing after it", async () => {
    const rounds = createDraftRounds();
    const a = pushStream();
    vi.stubGlobal("fetch", a.fetch);
    const r1 = rounds.begin();
    const mailA = makeComposer("A text.");
    const run1 = runDraftRound(ARGS, { instruction: "" }, { ...mailA.wiring, ...r1 });
    a.push(data({ type: "delta", kind: "content", text: "Draft for A" }));
    await vi.waitFor(() => expect(mailA.io.bodies).toEqual(["Draft for A"]));

    // The member opens mail B. The composer stays mounted and calls reset.
    rounds.reset();
    expect(r1.controller.signal.aborted).toBe(true);
    const before = { bodies: [...mailA.io.bodies], notice: mailA.io.notice, steps: mailA.io.steps };
    await expect(run1).resolves.toBeNull();
    expect(mailA.io.bodies).toEqual(before.bodies);
    expect(mailA.io.notice).toBe(before.notice);
    expect(mailA.io.steps).toBe(before.steps);
    expect(mailA.io.errors).toEqual([]);
  });

  it("a newer round ends the round before it, and only the newer one writes", async () => {
    const rounds = createDraftRounds();
    const a = pushStream();
    const b = pushStream();
    const fetches = [a.fetch, b.fetch];
    vi.stubGlobal("fetch", vi.fn((u: string, i?: RequestInit) => fetches.shift()!(u, i)));
    const shared = makeComposer("");

    const r1 = rounds.begin();
    const run1 = runDraftRound(ARGS, { instruction: "" }, { ...shared.wiring, ...r1 });
    a.push(data({ type: "delta", kind: "content", text: "Old" }));
    await vi.waitFor(() => expect(shared.composer.body).toBe("Old"));

    rounds.reset();
    const r2 = rounds.begin();
    const run2 = runDraftRound({ ...ARGS, body: "B text." }, { instruction: "" }, { ...shared.wiring, ...r2 });
    b.push(data({ type: "done", draft: "Draft for B" }));

    await expect(run1).resolves.toBeNull();
    await expect(run2).resolves.toBe("Draft for B");
    expect(shared.composer.body).toBe("Draft for B");
    expect(shared.io.bodies).toEqual(["Old", "Draft for B"]);
    expect(shared.io.notice).toBeNull();
  });
});

describe("email-draft-stop-grace (fix round 1, P2)", () => {
  it("Stop does nothing for 400 ms after a round starts, so a double-click on Draft does not stop it", () => {
    let t = 1_000;
    const rounds = createDraftRounds(() => t);
    const r = rounds.begin();
    t += STOP_GRACE_MS - 1;
    expect(rounds.stop()).toBe(false);
    expect(r.controller.signal.aborted).toBe(false);
    t += 1;
    expect(rounds.stop()).toBe(true);
    expect(r.controller.signal.aborted).toBe(true);
    expect(STOP_GRACE_MS).toBe(400);
  });
});

describe("email-draft-done-wins (fix round 1, P3)", () => {
  it("a Stop after `done` and before EOF keeps the draft", async () => {
    const s = pushStream();
    vi.stubGlobal("fetch", s.fetch);
    const { io, wiring } = makeIO();
    const run = runDraftRound(ARGS, { instruction: "" }, wiring);
    s.push(data({ type: "done", draft: "Finished" }));
    await new Promise((r) => setTimeout(r, 20));
    wiring.controller.abort();
    await expect(run).resolves.toBe("Finished");
    expect(io.bodies.at(-1)).toBe("Finished");
    expect(io.notice).toBeNull();
  });
});

describe("email-draft-split-data-line (verifier finding 3)", () => {
  it("a data line split across two reads still parses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'data: {"type":"delta","kind":"content","te',
          'xt":"Hel"}\n\ndata: {"type":"do',
          'ne","draft":"Split"}\n\n',
        ]),
      ),
    );
    const deltas: string[] = [];
    await expect(
      composeAssistStream(ARGS, { onDelta: (_k, t) => deltas.push(t) }),
    ).resolves.toEqual({ draft: "Split", skipped: undefined });
    expect(deltas).toEqual(["Hel"]);
  });
});

describe("email-draft-composers-end-the-round (fix round 2)", () => {
  // The panes stay mounted, so the hook's unmount abort never runs there. The
  // behaviour is fenced in `e2e/email-draft-switch.spec.ts`. This source check
  // runs in every vitest run.
  // Code only, as `draftAutosave.test.ts` reads it, so a reset that is
  // commented out fails.
  const read = (p: string) =>
    readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf-8").replace(/\r\n/g, "\n");
  const codeOnly = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
  const span = (src: string, from: string, to: string) => {
    const a = src.indexOf(from);
    const b = src.indexOf(to, a + from.length);
    expect(a, `missing: ${from}`).toBeGreaterThanOrEqual(0);
    expect(b, `missing: ${to}`).toBeGreaterThan(a);
    return src.slice(a, b);
  };
  const detail = codeOnly(read("../components/EmailDetail.tsx"));
  const compose = codeOnly(read("../components/ComposePanel.tsx"));

  it("EmailDetail resets the AI round on a mail switch, before the no-mail return", () => {
    // A delete or an archive of the open mail takes the `!email` branch.
    const branch = "if (!email) {\n      setDetail(null);";
    const end = detail.indexOf(branch);
    expect(end).toBeGreaterThanOrEqual(0);
    expect(detail.indexOf(branch, end + 1)).toBe(-1);
    expect(detail.slice(detail.lastIndexOf("useEffect(", end), end)).toContain("ai.reset();");
  });

  it("EmailDetail resets the AI round on a pop-out to the full composer", () => {
    expect(span(detail, "const popOutToComposer = ", "const handOver")).toContain("ai.reset();");
  });

  it("EmailDetail resets the AI round on a new reply", () => {
    expect(span(detail, "const startReply = (", "const switchReplyMode")).toContain("ai.reset();");
  });

  it("ComposePanel resets the AI round when it closes", () => {
    expect(span(compose, "if (!open) {", "return;")).toContain("ai.reset();");
  });
});
