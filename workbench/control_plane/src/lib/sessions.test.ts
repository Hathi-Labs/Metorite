/**
 * An empty server answer never erases the local chat cache (WS-27bm S15).
 *
 * Spec: project-docs/specs/projects_ai_chat.md §21.
 *
 * On production `chat_message` held no row, so every full fetch of a thread
 * answered `[]`. `fetchMessagesFromDb` then wrote that `[]` over the
 * localStorage cache, and the member's only copy of the thread was gone.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  bindChatScope,
  chatKey,
  fetchMessagesFromDb,
  getMessages,
  postMessagesWithRetry,
  queueSave,
  resetSaveLines,
  SAVE_RETRY_DELAYS_MS,
} from "./sessions";

/** The cache key of a session, in the namespace every test binds (PR #652). */
const KEY = (id: string) => chatKey("msgs", id) as string;

class MemoryStorage {
  private map = new Map<string, string>();
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
  clear(): void {
    this.map.clear();
  }
}

function answer(body: unknown, status = 200): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

const CACHED = [
  { id: "u1", role: "user", content: "hello", timestamp: 1 },
  { id: "a1", role: "assistant", content: "hi there", timestamp: 2 },
];

beforeEach(() => {
  const storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  bindChatScope("member@example.com|org-1");
});

afterEach(() => {
  bindChatScope(null);
  vi.unstubAllGlobals();
});

describe("fetchMessagesFromDb and the local cache", () => {
  it("uses the cache key the module reads", () => {
    // A wrong key here would make every case below pass for nothing.
    localStorage.setItem(KEY("s1"), JSON.stringify(CACHED));
    expect(getMessages("s1")).toHaveLength(2);
  });

  it("keeps a non-empty cache when the server answers []", async () => {
    localStorage.setItem(KEY("s1"), JSON.stringify(CACHED));
    answer([]);
    const got = await fetchMessagesFromDb("s1");
    expect(getMessages("s1").map((m) => m.id)).toEqual(["u1", "a1"]);
    expect(got.map((m) => m.id)).toEqual(["u1", "a1"]);
  });

  it("still writes a non-empty server answer over the cache", async () => {
    localStorage.setItem(KEY("s1"), JSON.stringify(CACHED));
    answer([
      { id: "u1", role: "user", content: "hello", timestamp: 1 },
      { id: "a1", role: "assistant", content: "hi there, longer", timestamp: 2 },
      { id: "u2", role: "user", content: "and more", timestamp: 3 },
    ]);
    await fetchMessagesFromDb("s1");
    expect(getMessages("s1").map((m) => m.id)).toEqual(["u1", "a1", "u2"]);
  });

  it("answers [] for an empty cache and an empty server", async () => {
    answer([]);
    expect(await fetchMessagesFromDb("s2")).toEqual([]);
  });

  it("never touches the cache on a paginated fetch", async () => {
    localStorage.setItem(KEY("s1"), JSON.stringify(CACHED));
    answer([]);
    expect(await fetchMessagesFromDb("s1", { limit: 10 })).toEqual([]);
    expect(getMessages("s1")).toHaveLength(2);
  });
});

// Fix round 1, P3 (§21.12). The first save can reach the gateway before the
// session row, and the foreign key answers 5xx. A retry lets the row land.
// Since 2026-10-09 the retries are bounded and each wait grows.
const NO_WAIT = [0, 0, 0];

function answers(...statuses: Array<number | "throw">) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      calls.push(String(init.body));
      const next = statuses.length > 1 ? statuses.shift() : statuses[0];
      if (next === "throw") throw new TypeError("fetch failed");
      return new Response("{}", { status: next ?? 200 });
    }),
  );
  return calls;
}

describe("postMessagesWithRetry", () => {
  it("retries after a 5xx, with the same body", async () => {
    const calls = answers(500, 200);
    expect(await postMessagesWithRetry("s1", "[1]", NO_WAIT)).toBe(200);
    expect(calls).toEqual(["[1]", "[1]"]);
  });

  it("retries after a network failure", async () => {
    const calls = answers("throw", 200);
    expect(await postMessagesWithRetry("s1", "[1]", NO_WAIT)).toBe(200);
    expect(calls).toHaveLength(2);
  });

  it("does not retry a 2xx or a 4xx", async () => {
    expect(answers(200)).toBeDefined();
    expect(await postMessagesWithRetry("s1", "[1]", NO_WAIT)).toBe(200);
    const calls = answers(403);
    expect(await postMessagesWithRetry("s1", "[1]", NO_WAIT)).toBe(403);
    expect(calls).toHaveLength(1);
  });

  it("stops after three retries and never throws", async () => {
    const calls = answers(500);
    expect(await postMessagesWithRetry("s1", "[1]", NO_WAIT)).toBe(500);
    expect(calls).toHaveLength(4);
    answers("throw");
    expect(await postMessagesWithRetry("s1", "[1]", NO_WAIT)).toBeNull();
  });

  it("waits longer before each retry", () => {
    expect(SAVE_RETRY_DELAYS_MS).toHaveLength(3);
    for (let i = 1; i < SAVE_RETRY_DELAYS_MS.length; i += 1) {
      expect(SAVE_RETRY_DELAYS_MS[i]).toBeGreaterThan(SAVE_RETRY_DELAYS_MS[i - 1]);
    }
  });
});

// The storm of 2026-10-09: a row the gateway could not store (a NUL in a tool
// result) answered 500, and AgentChat saves on every change of its list,
// about twelve times a second while a turn streams. The browser sent about
// 700 POSTs in one minute. The save line sends one request at a time, drops
// a body the server already holds, and pauses after the retries fail.
describe("the save line", () => {
  beforeEach(() => resetSaveLines());

  it("sends a body the server already stored only once", async () => {
    const calls = answers(200);
    await queueSave("s1", "[1]", NO_WAIT, 0);
    await queueSave("s1", "[1]", NO_WAIT, 0);
    expect(calls).toEqual(["[1]"]);
  });

  it("sends a burst as the first body and the newest one", async () => {
    const calls = answers(200);
    const first = queueSave("s1", "[1]", NO_WAIT, 0);
    for (let i = 2; i <= 30; i += 1) void queueSave("s1", `[${i}]`, NO_WAIT, 0);
    await first;
    expect(calls).toEqual(["[1]", "[30]"]);
  });

  it("bounds a burst that meets a 500 on every request", async () => {
    vi.useFakeTimers();
    try {
      const calls = answers(500);
      const first = queueSave("s1", "[1]", [10, 20, 40], 60_000);
      // Twelve saves a second for one minute, as the incident's client sent.
      for (let i = 0; i < 720; i += 1) {
        void queueSave("s1", `[x${i}]`, [10, 20, 40], 60_000);
        await vi.advanceTimersByTimeAsync(83);
      }
      // One body and its three retries, then the pause. When the pause ends,
      // the newest body and its retries: never one request per save.
      expect(calls.length).toBeLessThanOrEqual(8);
      expect(calls.slice(0, 4)).toEqual(["[1]", "[1]", "[1]", "[1]"]);
      await vi.advanceTimersByTimeAsync(120_000);
      await first;
      expect(calls.length).toBeLessThanOrEqual(12);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not resend a body the server refused with a 4xx", async () => {
    // A room member who may not send gets 403 (chat.py), and AgentChat saves
    // on every change of a live run: about twelve times a second.
    const calls = answers(403);
    for (let i = 0; i < 12; i += 1) await queueSave("s1", "[1]", NO_WAIT, 0);
    expect(calls).toEqual(["[1]"]);
    // A new body is a new question, and it is sent.
    await queueSave("s1", "[2]", NO_WAIT, 0);
    expect(calls).toEqual(["[1]", "[2]"]);
  });

  it("keeps each session's line apart", async () => {
    const calls = answers(200);
    await queueSave("s1", "[1]", NO_WAIT, 0);
    await queueSave("s2", "[1]", NO_WAIT, 0);
    expect(calls).toHaveLength(2);
  });
});
