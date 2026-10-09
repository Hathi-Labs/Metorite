/**
 * The chat draws a parked card again, from the server (WS-51 S2,
 * `chat_run_continuity.md` §4 S2).
 *
 * A run that parks, or dies, ends, and its end clears every blocking card in
 * the open chat. The question is a row on the server. These tests hold the
 * rule that turns those rows back into the chat's own card events, and the
 * chat's wiring of it, read as source (the runner is `environment: "node"`).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { cardsToRestore, fetchPendingAsks, type PendingAsk } from "./pendingAsks";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const ask = (over: Partial<PendingAsk> = {}): PendingAsk => ({
  requestId: "a".repeat(32),
  kind: "confirmation",
  event: "confirmation_requested",
  payload: { request_id: "a".repeat(32), title: "Send the invoice?" },
  askedAt: "2026-10-10T00:00:00+00:00",
  answerBy: "new_run",
  ...over,
});

describe("which cards come back", () => {
  it("a parked card comes back as its own event", () => {
    expect(cardsToRestore([ask()])).toEqual([
      { name: "confirmation_requested", value: { request_id: "a".repeat(32), title: "Send the invoice?" } },
    ]);
  });

  // Mutation: drop the `answerBy` check, and this fails.
  it("a card a live run still waits on is left to that run's stream", () => {
    expect(cardsToRestore([ask({ answerBy: "run" })])).toEqual([]);
  });

  // Mutation: drop the request-id check, and this fails.
  it("a row can only draw its own card", () => {
    expect(cardsToRestore([ask({ payload: { request_id: "b".repeat(32) } })])).toEqual([]);
  });

  it("only the cards the chat draws itself, and not one already on screen", () => {
    expect(cardsToRestore([ask({ event: "generative_ui" })])).toEqual([]);
    expect(cardsToRestore([ask()], new Set(["a".repeat(32)]))).toEqual([]);
  });
});

describe("the read", () => {
  it("asks the route for the thread, and drops a malformed row", async () => {
    const fetchFn = vi.fn(async () =>
      new Response(JSON.stringify([ask(), { requestId: "" }, null]), { status: 200 }),
    );
    const got = await fetchPendingAsks("t 1", fetchFn as unknown as typeof fetch);
    expect(fetchFn).toHaveBeenCalledWith("/api/chat/pending-asks?threadId=t%201", { cache: "no-store" });
    expect(got).toHaveLength(1);
  });

  it("is empty on any failure", async () => {
    const down = vi.fn(async () => { throw new Error("offline"); });
    expect(await fetchPendingAsks("t", down as unknown as typeof fetch)).toEqual([]);
    const refused = vi.fn(async () => new Response("no", { status: 403 }));
    expect(await fetchPendingAsks("t", refused as unknown as typeof fetch)).toEqual([]);
  });
});

describe("the chat's wiring", () => {
  const chat = read("../components/AgentChat.tsx");

  // Mutation: replay the cards through a second handler, and this fails.
  it("replays a returned card through the SAME handler as a streamed one", () => {
    expect(chat).toContain("useAgentEvents(hitlSubscriber);");
    expect(chat).toMatch(
      /for \(const card of cardsToRestore\(asks\)\) \{\s*hitlRef\.current\.onCustomEvent\?\.\(\{ name: card\.name, value: card\.value, threadId: forSession \}\);/,
    );
  });

  it("asks again when the chat opens, when a run ends, and when the poller says it waits", () => {
    expect(chat).toContain("}, [sessionId, threadNeedsInput, pendingAskTick]);");
    expect(chat).toContain("setPendingAskTick((n) => n + 1);");
    expect(chat).toContain("const threadNeedsInput = useThreadNeedsInput(sessionId);");
  });

  it("names the account that drew the card on every answer", () => {
    expect(chat).toContain("as: drawnFor");
    expect(chat).toContain('code: "answer_in_other_account"');
  });
});
