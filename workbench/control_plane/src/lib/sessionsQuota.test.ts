/**
 * A full storage never hangs a new chat (production bug, 2026-11-06).
 *
 * The owner pressed Studio → "Build it". The dock made a chat, and the server
 * saved it. The write of the chat list to localStorage hit the quota, and
 * `safeSetItem` dropped the error. The dock then looked for the chat in
 * storage, did not find it, and showed "Opening your chat…" for ever.
 *
 * R7 fence: this file. It fails when
 *   - `startFresh` stops returning the chat it made, or `chatToShow` stops
 *     falling back to it (the dock waits on storage again), or
 *   - a write that hits the quota stops freeing the oldest transcripts and
 *     trying once more (storage stays full), or
 *   - the eviction drops a send queue, the chat list, or the key it writes.
 */
import { readFileSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { chatToShow, startFresh } from "./railSessions";
import {
  __newPageForTests,
  bindChatScope,
  cacheMessages,
  chatKey,
  freeChatCacheRoom,
  getMessages,
  getQueue,
  getSessions,
  isQuotaError,
  saveQueue,
  type ChatSession,
} from "./sessions";

const SCOPE = "dev@fracktal.in|org-1";
const OTHER = "someone@else.in|org-2";

function quotaError(): Error {
  const e = new Error("The quota has been exceeded.");
  e.name = "QuotaExceededError";
  return e;
}

/** A storage with a quota in characters, as a browser counts it (key + value). */
class QuotaStorage {
  map = new Map<string, string>();
  constructor(public quota: number) {}
  get length(): number {
    return this.map.size;
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null;
  }
  used(): number {
    let n = 0;
    for (const [k, v] of this.map) n += k.length + v.length;
    return n;
  }
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    const prior = this.map.has(k) ? k.length + (this.map.get(k) as string).length : 0;
    if (this.used() - prior + k.length + v.length > this.quota) throw quotaError();
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
  clear(): void {
    this.map.clear();
  }
}

let storage: QuotaStorage;

function useStorage(s: QuotaStorage): void {
  storage = s;
  vi.stubGlobal("window", { localStorage: s });
  vi.stubGlobal("localStorage", s);
}

function chat(id: string, updatedAt: string): ChatSession {
  return { id, name: id, agentName: "orchestrator", createdAt: updatedAt, updatedAt, messageCount: 4 };
}

const BIG = "x".repeat(10_000);

/** Fill the bound namespace with old transcripts, and the gap after them. */
function fillWithOldChats(count: number): void {
  const list: ChatSession[] = [];
  for (let i = 0; i < count; i++) {
    const at = new Date(Date.UTC(2026, 0, 1 + i)).toISOString();
    list.push(chat(`old-${i}`, at));
    storage.map.set(chatKey("msgs", `old-${i}`) as string, BIG);
  }
  storage.map.set(chatKey("sessions") as string, JSON.stringify(list));
  storage.map.set(chatKey("queue", "old-0") as string, JSON.stringify(["unsent words"]));
  storage.map.set("pad", "p".repeat(Math.max(0, storage.quota - storage.used() - "pad".length)));
}

beforeEach(() => {
  __newPageForTests();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  vi.stubGlobal("crypto", { randomUUID: () => `new-${Math.random().toString(36).slice(2)}` });
  useStorage(new QuotaStorage(200_000));
  bindChatScope(SCOPE);
});

afterEach(() => {
  vi.unstubAllGlobals();
  bindChatScope(null);
});

describe("a fresh chat when storage is full", () => {
  it("shows the chat it made, even when storage refuses every write", () => {
    const refusing = new QuotaStorage(0);
    refusing.setItem = () => {
      throw quotaError();
    };
    useStorage(refusing);

    const pick = startFresh("orchestrator");

    // Storage holds nothing, which is the production state.
    expect(getSessions()).toEqual([]);
    // The dock still has a chat to show: no wait on storage.
    const shown = chatToShow(getSessions(), pick.activeId, pick.session);
    expect(shown?.id).toBe(pick.activeId);
    expect(shown?.agentName).toBe("orchestrator");
  });

  it("prefers the stored row, and names nothing for an unknown id", () => {
    const stored = chat("a", "2026-11-06T10:00:00.000Z");
    const made = { ...stored, name: "made here" };
    expect(chatToShow([stored], "a", made)).toBe(stored);
    expect(chatToShow([], "a", made)).toBe(made);
    expect(chatToShow([], "b", made)).toBeNull();
    expect(chatToShow([stored], "", made)).toBeNull();
  });

  it("frees the oldest transcripts, so the new chat lands in the list", () => {
    fillWithOldChats(15);

    const pick = startFresh("orchestrator");

    expect(getSessions().map((s) => s.id)).toContain(pick.activeId);
    // The oldest chat lost its cache, and the newest kept it.
    expect(storage.map.has(chatKey("msgs", "old-0") as string)).toBe(false);
    expect(storage.map.has(chatKey("msgs", "old-14") as string)).toBe(true);
    // The send queue stays: it holds words the member has not sent.
    expect(getQueue("old-0")).toEqual(["unsent words"]);
    // The list keeps every chat: only caches go.
    expect(getSessions()).toHaveLength(16);
  });

  it("frees room for a transcript write, and keeps the transcript it writes", () => {
    fillWithOldChats(15);
    const fresh = [{ id: "u1", role: "user", content: "h".repeat(5_000), timestamp: 1 }];

    cacheMessages("new-chat", fresh);

    expect(getMessages("new-chat")).toEqual(fresh);
    expect(storage.map.has(chatKey("msgs", "old-0") as string)).toBe(false);
    expect(storage.map.has(chatKey("msgs", "old-14") as string)).toBe(true);
  });

  it("saves a send queue in a full storage", () => {
    fillWithOldChats(15);
    saveQueue("old-3", ["a", "b"]);
    expect(getQueue("old-3")).toEqual(["a", "b"]);
    expect(getQueue("old-0")).toEqual(["unsent words"]);
  });

  it("frees the bound member's caches before another member's", () => {
    fillWithOldChats(15);
    const otherKey = chatKey("msgs", "theirs", OTHER) as string;
    storage.map.set(otherKey, "y".repeat(100));

    const dropped = freeChatCacheRoom("none", 100);

    expect(dropped).toBeGreaterThan(0);
    expect(storage.getItem(otherKey)).not.toBeNull();
    expect(storage.getItem(chatKey("sessions") as string)).not.toBeNull();
  });

  it("does nothing when no transcript is cached", () => {
    expect(freeChatCacheRoom("none", 10)).toBe(0);
  });
});

describe("the dock wiring (source fence)", () => {
  const dock = readFileSync(new URL("./shell/AssistantDock.tsx", import.meta.url), "utf-8");

  it("keeps the chat that startFresh made, and shows it when storage lost it", () => {
    expect(dock).toMatch(/madeHere\.current\.set\(pick\.session\.id/);
    expect(dock).toMatch(/chatToShow\(sessions, activeId, lastMade\)/);
  });

  it("never looks up its own new chat as if a link named it", () => {
    expect(dock).toMatch(/if \(madeHere\.current\.has\(id\)\) return;/);
  });
});

describe("isQuotaError", () => {
  it("knows the quota error of each browser, and nothing else", () => {
    expect(isQuotaError(quotaError())).toBe(true);
    expect(isQuotaError({ name: "NS_ERROR_DOM_QUOTA_REACHED" })).toBe(true);
    expect(isQuotaError({ code: 22 })).toBe(true);
    expect(isQuotaError(new TypeError("no storage"))).toBe(false);
    expect(isQuotaError(null)).toBe(false);
  });
});
