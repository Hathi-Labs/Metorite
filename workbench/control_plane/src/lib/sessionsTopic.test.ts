/**
 * The client act of a move (WS-CO CO-2b, `chat_organization.md` §6).
 *
 * `setSessionTopic` writes the topic at once, sends one PUT to the BFF with
 * the topic id only, takes the server's name, and puts the old topic back
 * when the server refuses. The merge of the server's list carries a move to
 * a second device. Fence (R7): this file. Mutations, each red here: send a
 * key beside `topic_id`; skip the undo on a refusal; ignore the server's
 * `topicId` in the merge.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __newPageForTests,
  bindChatScope,
  chatKey,
  fetchAndMergeSessionsFromDb,
  getSessions,
  setSessionTopic,
  type ChatSession,
} from "./sessions";

const SCOPE = "priya@example.com|org1";
const store = new Map<string, string>();
const storage = {
  get length() {
    return store.size;
  },
  key: (i: number) => [...store.keys()][i] ?? null,
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, String(v)),
  removeItem: (k: string) => void store.delete(k),
};

function seed(...rows: ChatSession[]): void {
  store.set(chatKey("sessions") as string, JSON.stringify(rows));
}

function row(id: string, over: Partial<ChatSession> = {}): ChatSession {
  return {
    id,
    name: id,
    agentName: "orchestrator",
    createdAt: "2026-11-17T09:00:00.000Z",
    updatedAt: "2026-11-17T09:00:00.000Z",
    messageCount: 2,
    title: `Chat ${id}`,
    ...over,
  };
}

const topicOf = (id: string) => {
  const s = getSessions().find((x) => x.id === id);
  return [s?.topicId ?? null, s?.topicName ?? null];
};

beforeEach(() => {
  store.clear();
  __newPageForTests();
  vi.stubGlobal("window", { localStorage: storage, dispatchEvent: () => true });
  vi.stubGlobal("localStorage", storage);
  bindChatScope(SCOPE);
});

afterEach(() => {
  vi.unstubAllGlobals();
  bindChatScope(null);
});

describe("setSessionTopic (CO-2b)", () => {
  it("writes the topic at once, PUTs the id only, and takes the server's name", async () => {
    seed(row("a"));
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, topicId: "t-1", topicName: "Sales team" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setSessionTopic("a", { id: "t-1", name: "Sales" })).toBe(true);
    expect(topicOf("a")).toEqual(["t-1", "Sales"]);
    await vi.waitFor(() => expect(topicOf("a")).toEqual(["t-1", "Sales team"]));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/chat/sessions/a/topic");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ topic_id: "t-1" });
  });

  it("No topic sends null", async () => {
    seed(row("a", { topicId: "t-1", topicName: "Sales" }));
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, topicId: null, topicName: null }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setSessionTopic("a", null)).toBe(true);
    expect(topicOf("a")).toEqual([null, null]);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ topic_id: null });
  });

  it("a refusal puts the old topic back and draws the list again", async () => {
    seed(row("a", { topicId: "t-1", topicName: "Sales" }));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Topic not found" }), { status: 404 })));
    const undo = vi.fn();
    expect(setSessionTopic("a", { id: "t-2", name: "Board prep" }, undo)).toBe(true);
    await vi.waitFor(() => expect(undo).toHaveBeenCalledTimes(1));
    expect(topicOf("a")).toEqual(["t-1", "Sales"]);
  });

  it("a lost connection puts the old topic back too", async () => {
    seed(row("a"));
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    const undo = vi.fn();
    expect(setSessionTopic("a", { id: "t-2", name: "Board prep" }, undo)).toBe(true);
    await vi.waitFor(() => expect(undo).toHaveBeenCalledTimes(1));
    expect(topicOf("a")).toEqual([null, null]);
  });

  it("the same topic, or a chat that is not in the list, sends nothing", () => {
    seed(row("a", { topicId: "t-1", topicName: "Sales" }), row("b"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(setSessionTopic("a", { id: "t-1", name: "Sales" })).toBe(false);
    expect(setSessionTopic("b", null)).toBe(false);
    expect(setSessionTopic("nope", { id: "t-1", name: "Sales" })).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the merge carries a move to a second device (CO-2b)", () => {
  it("takes the server's topic when it sends one, and keeps the mark when it sends none", async () => {
    seed(row("a"), row("b", { topicId: "t-old", topicName: "Old" }), row("c", { topicId: "t-gone", topicName: "Gone" }));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { ...row("a"), topicId: "t-1", topicName: "Sales" },
      // The switch is off for this answer: no topic key.
      { ...row("b") },
      // The topic no longer shows (an admin hid it): the server sends null.
      { ...row("c"), topicId: null, topicName: null },
      { ...row("d"), topicId: "t-2", topicName: "Board prep" },
    ]), { status: 200 })));
    await fetchAndMergeSessionsFromDb();
    expect(topicOf("a")).toEqual(["t-1", "Sales"]);
    expect(topicOf("b")).toEqual(["t-old", "Old"]);
    expect(topicOf("c")).toEqual([null, null]);
    expect(topicOf("d")).toEqual(["t-2", "Board prep"]);
  });
});
