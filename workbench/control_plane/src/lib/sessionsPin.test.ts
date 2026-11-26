/**
 * The client act of a pin (WS-CO CO-11, `chat_organization.md` §18).
 *
 * `setSessionPinned` writes the mark at once, sends one request to the BFF,
 * and puts the mark back when the server refuses. The merge of the server's
 * list carries a pin to a second device. Fence (R7): this file.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __newPageForTests,
  bindChatScope,
  chatKey,
  fetchAndMergeSessionsFromDb,
  getSessions,
  setSessionPinned,
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
    createdAt: "2026-11-16T09:00:00.000Z",
    updatedAt: "2026-11-16T09:00:00.000Z",
    messageCount: 2,
    title: `Chat ${id}`,
    ...over,
  };
}

const mark = (id: string) => getSessions().find((s) => s.id === id)?.pinnedAt ?? null;

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

describe("setSessionPinned (CO-11)", () => {
  it("writes the mark at once, PUTs once, and keeps the server's time", async () => {
    seed(row("a"));
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ pinned: true, pinnedAt: "2026-11-16T10:00:00+00:00" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setSessionPinned("a", true)).toBe(true);
    expect(mark("a")).not.toBeNull();
    await vi.waitFor(() => expect(mark("a")).toBe("2026-11-16T10:00:00+00:00"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]).toEqual(["/api/chat/sessions/a/pin", { method: "PUT" }]);
  });

  it("an unpin sends DELETE", async () => {
    seed(row("a", { pinnedAt: "2026-11-16T10:00:00Z" }));
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ pinned: false, pinnedAt: null }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(setSessionPinned("a", false)).toBe(true);
    expect(mark("a")).toBeNull();
    expect(fetchMock.mock.calls[0]).toEqual(["/api/chat/sessions/a/pin", { method: "DELETE" }]);
  });

  it("a refusal puts the mark back and draws the list again", async () => {
    seed(row("a"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    const undo = vi.fn();
    expect(setSessionPinned("a", true, undo)).toBe(true);
    await vi.waitFor(() => expect(undo).toHaveBeenCalledTimes(1));
    expect(mark("a")).toBeNull();
  });

  it("an archived chat takes no pin, and a pin that is there changes nothing", () => {
    seed(row("put-away", { archivedAt: "2026-11-16T09:30:00Z" }), row("b", { pinnedAt: "2026-11-16T10:00:00Z" }));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(setSessionPinned("put-away", true)).toBe(false);
    expect(setSessionPinned("b", true)).toBe(false);
    expect(setSessionPinned("nope", true)).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the merge carries a pin to a second device (CO-11)", () => {
  it("takes the server's pin when it sends one, and keeps the mark when it sends none", async () => {
    seed(row("a"), row("b", { pinnedAt: "2026-11-16T08:00:00Z" }));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { ...row("a"), pinned: true, pinnedAt: "2026-11-16T10:00:00+00:00" },
      // The switch is off for this answer: no pin key.
      { ...row("b") },
      { ...row("c"), pinned: true, pinnedAt: "2026-11-16T11:00:00+00:00" },
    ]), { status: 200 })));
    await fetchAndMergeSessionsFromDb();
    expect(mark("a")).toBe("2026-11-16T10:00:00+00:00");
    expect(mark("b")).toBe("2026-11-16T08:00:00Z");
    expect(mark("c")).toBe("2026-11-16T11:00:00+00:00");
  });
});
