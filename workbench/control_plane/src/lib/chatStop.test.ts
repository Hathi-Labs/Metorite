/**
 * UXD-4 — the chat's Stop says "stopped" only after the server stopped the
 * run, and a failed cancel leaves the run live with a reason and a Retry.
 *
 * R7 fences named here:
 *   * `chat-stop-waits-for-server`: Stop marks `stopping` and keeps the run
 *     live. The chat goes idle only on an `ok` answer.
 *   * `chat-stop-failure-stays-live`: a failed cancel (a network fault, a
 *     5xx, a 403) leaves `isLoading` true and sets the reason.
 *   * `chat-edit-waits-for-stop`: an edit does not start a new run over a
 *     run whose cancel failed.
 *   * `chat-stop-ui`: the composer shows the work on the Stop button, the
 *     reason with Retry, and Esc does the same as the button.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { submitEdit } from "./chatEdit";
import { cancelRun, stopConfirmed, stopFailed, stopStarted, stopFailureText } from "./chatStop";
import type { SessionStreamState } from "./chatStore";
import { IDLE_RUN } from "./runStatus";

const live: SessionStreamState = {
  messages: [],
  isLoading: true,
  error: null,
  abortController: null,
  recovering: false,
  lastEventId: null,
  runStatus: "running",
  outage: false,
  pendingSends: [],
  pendingResume: [],
  run: IDLE_RUN,
  stopEpoch: 2,
  steerOwed: true,
};

const answer = (status: number, body = "{}") =>
  (async () => new Response(body, { status })) as unknown as typeof fetch;

describe("chat-stop-waits-for-server", () => {
  it("Stop marks stopping and keeps the run live", () => {
    const s = stopStarted({ ...live, stopError: "old" });
    expect(s.stopping).toBe(true);
    expect(s.stopError).toBeNull();
    expect(s.isLoading).toBe(true);
    expect(s.runStatus).toBe("running");
  });

  it("only an ok answer stops the run in the UI", async () => {
    expect(await cancelRun("t1", answer(200, '{"ok":true}'))).toEqual({ ok: true });
    const s = stopConfirmed(stopStarted(live));
    expect(s).toMatchObject({
      isLoading: false,
      runStatus: "idle",
      stopping: false,
      stopError: null,
      steerOwed: false,
      stopEpoch: 3,
    });
  });
});

describe("chat-stop-failure-stays-live", () => {
  it("a network fault leaves the run live with the reason", async () => {
    const out = await cancelRun("t1", (() => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch);
    expect(out).toEqual({ ok: false, reason: stopFailureText(undefined) });
    const s = stopFailed(stopStarted(live), (out as { reason: string }).reason);
    expect(s.isLoading).toBe(true);
    expect(s.runStatus).toBe("running");
    expect(s.stopping).toBe(false);
    expect(s.stopError).toMatch(/^Could not stop the run/);
  });

  it("a 5xx says to try again", async () => {
    expect(await cancelRun("t1", answer(502))).toEqual({ ok: false, reason: "Could not stop the run. Try again." });
  });

  it("a 403 gives the server's words", async () => {
    const body = JSON.stringify({ detail: "You are watching this conversation and cannot stop its run." });
    expect(await cancelRun("t1", answer(403, body))).toEqual({
      ok: false,
      reason: "You are watching this conversation and cannot stop its run.",
    });
  });
});

describe("chat-edit-waits-for-stop", () => {
  const msgs = [{ id: "u1", role: "user", content: "hi", timestamp: 1 }] as never;

  it("refuses the edit while the run whose cancel failed is live", async () => {
    const send = vi.fn();
    const out = await submitEdit(
      { threadId: "t", getMessages: () => msgs, isRunning: () => true, stop: async () => "Could not stop the run. Try again.", send },
      "u1",
      "hello",
    );
    expect(out).toEqual({ ok: false, reason: "Could not stop the run. Try again." });
    expect(send).not.toHaveBeenCalled();
  });

  it("sends once the stop landed", async () => {
    const send = vi.fn(async () => ({ ok: true }) as const);
    const out = await submitEdit(
      { threadId: "t", getMessages: () => msgs, isRunning: () => true, stop: async () => null, send },
      "u1",
      "hello",
    );
    expect(out).toEqual({ ok: true });
    expect(send).toHaveBeenCalledOnce();
  });
});

describe("chat-stop-ui", () => {
  const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

  it("the hook waits for cancelRun before it goes idle", () => {
    const hook = read("../hooks/useAgentChat.ts");
    const body = hook.slice(hook.indexOf("const stopGeneration = useCallback"));
    const started = body.indexOf("stopStarted");
    const asked = body.indexOf("cancelRun(threadId)");
    const idle = body.indexOf("stopConfirmed");
    expect(started).toBeGreaterThan(-1);
    expect(asked).toBeGreaterThan(started);
    expect(idle).toBeGreaterThan(asked);
    // The local stream is aborted only after the server answered.
    expect(body.indexOf(".abort()")).toBeGreaterThan(asked);
    // A late answer never stops a newer run's stream.
    expect(body).toMatch(/if \(streamNow && streamNow !== stoppedStream\) \{/);
    expect(body.indexOf("streamNow !== stoppedStream")).toBeLessThan(body.indexOf(".abort()"));
  });

  it("the composer shows Stopping, the reason and Retry; Esc uses the same handler", () => {
    const chat = read("../components/AgentChat.tsx");
    expect(chat).toContain("loading={primary === \"stop\" && stopping}");
    expect(chat).toContain("data-chat-stop-error");
    expect(chat).toMatch(/onClick=\{handleStop\}[^>]*>\s*Retry/);
    expect(chat).toMatch(/e\.key === "Escape"[\s\S]{0,200}handleStop\(\)/);
    expect(chat).toMatch(/if \(stopping\) return;/);
  });
});
