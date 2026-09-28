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

import { fetchMessagesFromDb, getMessages } from "./sessions";

const KEY = "cc-msgs-";

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
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchMessagesFromDb and the local cache", () => {
  it("uses the cache key the module reads", () => {
    // A wrong key here would make every case below pass for nothing.
    localStorage.setItem(KEY + "s1", JSON.stringify(CACHED));
    expect(getMessages("s1")).toHaveLength(2);
  });

  it("keeps a non-empty cache when the server answers []", async () => {
    localStorage.setItem(KEY + "s1", JSON.stringify(CACHED));
    answer([]);
    const got = await fetchMessagesFromDb("s1");
    expect(getMessages("s1").map((m) => m.id)).toEqual(["u1", "a1"]);
    expect(got.map((m) => m.id)).toEqual(["u1", "a1"]);
  });

  it("still writes a non-empty server answer over the cache", async () => {
    localStorage.setItem(KEY + "s1", JSON.stringify(CACHED));
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
    localStorage.setItem(KEY + "s1", JSON.stringify(CACHED));
    answer([]);
    expect(await fetchMessagesFromDb("s1", { limit: 10 })).toEqual([]);
    expect(getMessages("s1")).toHaveLength(2);
  });
});
