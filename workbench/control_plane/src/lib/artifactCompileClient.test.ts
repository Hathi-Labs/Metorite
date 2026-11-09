// The browser half of the React artifact compile (task 2432 fix round).
// A passing refusal ("busy") must not end a card as "failed to build", and
// a chat with many new cards must not end with sticky errors (live: 16 cards
// at once gave 9 "busy").
//
// Mutations, each turns a named case red:
//   - treat `busy` as final -> "retries busy after its Retry-After …".
//   - auto-retry `rate_limited` -> "shows the member's limit sentence …".
//   - send the burst without the queue -> "a burst of 16 new cards …".
import { describe, expect, it, vi } from "vitest";

import {
  classifyCompileAnswer,
  compileWithRetry,
  COULD_NOT_REACH,
  DEFAULT_RETRY_AFTER_MS,
  JITTER_MS,
  MAX_RETRIES,
  MAX_RETRY_AFTER_MS,
  requestCompile,
  retryAfterMs,
  serialQueue,
  type CompileDeps,
} from "./artifactCompileClient";

type Answer = { status: number; body: unknown; retryAfter: string | null };

const OK: Answer = { status: 200, body: { ok: true, js: "console.log(1)", bytes: 14 }, retryAfter: null };
const BUSY: Answer = {
  status: 429,
  body: { ok: false, code: "busy", error: "Metorite is building other artifacts right now. Wait a few seconds and try again." },
  retryAfter: "5",
};
const MEMBER_BUSY: Answer = {
  status: 429,
  body: { ok: false, code: "member_busy", error: "One of your artifacts is still building." },
  retryAfter: "2",
};
const LIMITED: Answer = {
  status: 429,
  body: { ok: false, code: "rate_limited", error: "You built many artifacts in the last 10 minutes, which reached the limit. Try again in 4 minutes." },
  retryAfter: "230",
};
const DOWN: Answer = { status: 503, body: null, retryAfter: null };

/** Deps that answer from a script, and record each sleep. */
function scripted(answers: Array<Answer | Error>, random = 0.5) {
  const sleeps: number[] = [];
  let posts = 0;
  const deps: CompileDeps = {
    post: async () => {
      const next = answers[Math.min(posts, answers.length - 1)];
      posts += 1;
      if (next instanceof Error) throw next;
      return next;
    },
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    random: () => random,
  };
  return { deps, sleeps, posts: () => posts };
}

describe("classifyCompileAnswer — each status path", () => {
  it("200 with a bundle is ok", () => {
    expect(classifyCompileAnswer(200, OK.body, null)).toEqual({ kind: "ok", js: "console.log(1)" });
  });

  it("200 with ok:false is the source's fault: final, failed to build", () => {
    expect(classifyCompileAnswer(200, { ok: false, reason: "timeout", error: "stopped" }, null)).toEqual({
      kind: "error",
      error: "stopped",
    });
  });

  it("429 busy and 429 member_busy pass: retry after their Retry-After", () => {
    expect(classifyCompileAnswer(BUSY.status, BUSY.body, "5")).toMatchObject({ kind: "retry", retryAfterMs: 5_000 });
    expect(classifyCompileAnswer(MEMBER_BUSY.status, MEMBER_BUSY.body, "2")).toMatchObject({
      kind: "retry",
      retryAfterMs: 2_000,
    });
  });

  it("503 (an unavailable limit or server) passes too", () => {
    expect(classifyCompileAnswer(503, { ok: false, code: "limit_unavailable", error: "x" }, "2")).toMatchObject({
      kind: "retry",
      retryAfterMs: 2_000,
    });
    expect(classifyCompileAnswer(503, null, null)).toMatchObject({ kind: "retry", retryAfterMs: DEFAULT_RETRY_AFTER_MS });
  });

  it("429 rate_limited is the member's limit: no retry, the server's sentence", () => {
    expect(classifyCompileAnswer(LIMITED.status, LIMITED.body, "230")).toEqual({
      kind: "limited",
      error: (LIMITED.body as { error: string }).error,
    });
  });

  it("413, 400 and 403 are final, with the server's words", () => {
    expect(classifyCompileAnswer(413, { ok: false, code: "too_large", error: "too big" }, null)).toEqual({
      kind: "error",
      error: "too big",
    });
    expect(classifyCompileAnswer(403, null, null).kind).toBe("error");
  });

  it("bounds Retry-After, and falls back on a missing or odd one", () => {
    expect(retryAfterMs(null)).toBe(DEFAULT_RETRY_AFTER_MS);
    expect(retryAfterMs("soon")).toBe(DEFAULT_RETRY_AFTER_MS);
    expect(retryAfterMs("0")).toBe(250);
    expect(retryAfterMs("3600")).toBe(MAX_RETRY_AFTER_MS);
  });
});

describe("compileWithRetry", () => {
  it("retries busy after its Retry-After, with jitter, and keeps going to the bundle", async () => {
    const s = scripted([BUSY, MEMBER_BUSY, OK], 0.5);
    const out = await compileWithRetry("x", s.deps);
    expect(out).toEqual({ kind: "ok", js: "console.log(1)" });
    expect(s.posts()).toBe(3);
    const jitter = Math.floor(0.5 * JITTER_MS);
    expect(s.sleeps).toEqual([5_000 + jitter, 2_000 + jitter]);
  });

  it("stops after the last retry as unavailable, never as failed to build", async () => {
    const s = scripted([BUSY]);
    const out = await compileWithRetry("x", s.deps);
    expect(out.kind).toBe("unavailable");
    expect(s.posts()).toBe(MAX_RETRIES + 1);
    expect(s.sleeps).toHaveLength(MAX_RETRIES);
  });

  it("retries a 503 and a request that got no answer", async () => {
    const s = scripted([DOWN, new TypeError("Failed to fetch"), OK]);
    expect((await compileWithRetry("x", s.deps)).kind).toBe("ok");
    expect(s.posts()).toBe(3);
    const t = scripted([new TypeError("Failed to fetch")]);
    expect(await compileWithRetry("x", t.deps)).toEqual({ kind: "unavailable", error: COULD_NOT_REACH });
  });

  it("shows the member's limit sentence at once, and never retries it", async () => {
    const s = scripted([LIMITED, OK]);
    const out = await compileWithRetry("x", s.deps);
    expect(out).toEqual({ kind: "limited", error: (LIMITED.body as { error: string }).error });
    expect(s.posts()).toBe(1);
    expect(s.sleeps).toEqual([]);
  });

  it("does not retry a source error", async () => {
    const s = scripted([{ status: 200, body: { ok: false, error: "line 1: bad" }, retryAfter: null }, OK]);
    expect(await compileWithRetry("x", s.deps)).toEqual({ kind: "error", error: "line 1: bad" });
    expect(s.posts()).toBe(1);
  });
});

describe("serialQueue", () => {
  it("runs one task at a time, in order, and goes on after a failure", async () => {
    const run = serialQueue();
    const seen: string[] = [];
    let live = 0;
    let peak = 0;
    const task = (name: string, fail = false) => async () => {
      live += 1;
      peak = Math.max(peak, live);
      await new Promise((r) => setTimeout(r, 2));
      seen.push(name);
      live -= 1;
      if (fail) throw new Error(name);
      return name;
    };
    const results = await Promise.allSettled([run(task("a")), run(task("b", true)), run(task("c"))]);
    expect(seen).toEqual(["a", "b", "c"]);
    expect(peak).toBe(1);
    expect(results.map((r) => r.status)).toEqual(["fulfilled", "rejected", "fulfilled"]);
  });
});

describe("a burst of new cards", () => {
  /**
   * A server like the real one for ONE member: a second build of the member
   * while one runs gets 429 member_busy.
   */
  function oneBuildServer() {
    let live = 0;
    let refused = 0;
    let peak = 0;
    const deps: CompileDeps = {
      post: async (code) => {
        if (live > 0) {
          refused += 1;
          return MEMBER_BUSY;
        }
        live += 1;
        peak = Math.max(peak, live);
        await new Promise((r) => setTimeout(r, 1));
        live -= 1;
        return { status: 200, body: { ok: true, js: `/* ${code} */` }, retryAfter: null };
      },
      sleep: vi.fn(async () => undefined),
      random: () => 0,
    };
    return { deps, refused: () => refused, peak: () => peak };
  }

  it("a burst of 16 new cards builds every card, one at a time, with no refusal", async () => {
    const server = oneBuildServer();
    const codes = Array.from({ length: 16 }, (_, i) => `export default () => ${i} // burst-${Date.now()}`);
    const outcomes = await Promise.all(codes.map((c) => requestCompile(c, server.deps)));
    expect(outcomes.map((o) => o.kind)).toEqual(Array(16).fill("ok"));
    expect(server.peak()).toBe(1);
    expect(server.refused()).toBe(0);
  });

  it("the same burst with no queue meets the refusals the queue removes", async () => {
    const server = oneBuildServer();
    const outcomes = await Promise.all(
      Array.from({ length: 16 }, (_, i) => compileWithRetry(`no-queue-${i}`, server.deps)),
    );
    expect(server.refused()).toBeGreaterThan(0);
    // The retries still carry most cards through: no card says "failed".
    expect(outcomes.some((o) => o.kind === "error")).toBe(false);
  });

  it("asks once for a source that two cards show, and keeps the bundle", async () => {
    const s = scripted([OK]);
    const code = `export default () => "twin" // ${Date.now()}`;
    const [a, b] = await Promise.all([requestCompile(code, s.deps), requestCompile(code, s.deps)]);
    expect(a.kind === "ok" && b.kind === "ok").toBe(true);
    expect(s.posts()).toBe(1);
    await requestCompile(code, s.deps);
    expect(s.posts()).toBe(1);
  });
});
