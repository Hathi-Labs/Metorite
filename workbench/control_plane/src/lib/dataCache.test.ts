import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FRESH_MS,
  MAX_ENTRIES,
  bindIdentity,
  cacheKey,
  clearAll,
  identity,
  invalidate,
  isFresh,
  peek,
  put,
  read,
  restoreClock,
  setClock,
  size,
  subscribe,
} from "./dataCache";

afterEach(() => {
  clearAll();
  restoreClock();
});

describe("cacheKey", () => {
  it("sorts the parameters, so one answer never gets two keys", () => {
    // The whole cache turns on this. `?a=1&b=2` and `?b=2&a=1` are the same
    // read, and two keys means a MISS on the way back — the exact wait this
    // module exists to remove.
    expect(cacheKey("tasks", { b: 2, a: 1 })).toBe(cacheKey("tasks", { a: 1, b: 2 }));
  });

  it("drops empty parameters rather than keying on them", () => {
    expect(cacheKey("tasks", { a: 1, q: "", z: undefined })).toBe("tasks?a=1");
  });

  it("keeps a bare path bare", () => {
    expect(cacheKey("tree")).toBe("tree");
  });
});

describe("the two rules", () => {
  it("RULE 1 — serves a stored value at ANY age", () => {
    let now = 1_000;
    setClock(() => now);
    put("tree", { rows: [1] });
    now += 60 * 60 * 1000; // an hour later
    expect(peek("tree")?.data).toEqual({ rows: [1] });
  });

  it("RULE 2 — an unfresh entry still triggers a real fetch", async () => {
    let now = 1_000;
    setClock(() => now);
    put("tree", "old");
    now += FRESH_MS + 1;
    const fetcher = vi.fn(async () => "new");
    await expect(read("tree", fetcher)).resolves.toBe("new");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("skips the fetch INSIDE the fresh window", async () => {
    let now = 1_000;
    setClock(() => now);
    put("tree", "cached");
    now += FRESH_MS - 1;
    const fetcher = vi.fn(async () => "new");
    await expect(read("tree", fetcher)).resolves.toBe("cached");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("`force` asks even inside the fresh window", async () => {
    put("tree", "cached");
    const fetcher = vi.fn(async () => "new");
    await expect(read("tree", fetcher, { force: true })).resolves.toBe("new");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("isFresh", () => {
  it("is exclusive at the boundary", () => {
    const entry = { data: 1, storedAt: 0 };
    expect(isFresh(entry, FRESH_MS - 1)).toBe(true);
    expect(isFresh(entry, FRESH_MS)).toBe(false);
  });
});

describe("deduplication", () => {
  it("three callers at once make ONE request", async () => {
    let release: (v: string) => void = () => {};
    const fetcher = vi.fn(
      () => new Promise<string>((res) => { release = res; })
    );
    const all = Promise.all([
      read("tree", fetcher),
      read("tree", fetcher),
      read("tree", fetcher),
    ]);
    release("once");
    await expect(all).resolves.toEqual(["once", "once", "once"]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("releases the slot, so a LATER read fetches again", async () => {
    const fetcher = vi.fn(async () => "v");
    await read("k", fetcher);
    await read("k", fetcher, { force: true });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("releases the slot after a FAILURE too — a rejected read must not wedge the key", async () => {
    const bad = vi.fn(async () => { throw new Error("boom"); });
    await expect(read("k", bad)).rejects.toThrow("boom");
    const good = vi.fn(async () => "ok");
    await expect(read("k", good)).resolves.toBe("ok");
    expect(good).toHaveBeenCalledTimes(1);
  });
});

describe("a failed revalidation", () => {
  it("KEEPS the last known good value", async () => {
    // The classic version of this bug blanks the screen at the moment the user
    // most needs to see something. The error travels; the data stays.
    let now = 1_000;
    setClock(() => now);
    put("tree", "good");
    now += FRESH_MS + 1;
    await expect(
      read("tree", async () => { throw new Error("gateway 500"); })
    ).rejects.toThrow("gateway 500");
    expect(peek("tree")?.data).toBe("good");
  });
});

describe("invalidate", () => {
  it("drops a whole family by prefix and leaves the rest", () => {
    put("projects/tasks?a=1", 1);
    put("projects/tasks?a=2", 2);
    put("projects/tree", 3);
    expect(invalidate("projects/tasks")).toBe(2);
    expect(peek("projects/tasks?a=1")).toBeUndefined();
    expect(peek("projects/tree")?.data).toBe(3);
  });

  it("accepts a predicate for what a prefix cannot say", () => {
    put("a/1", 1);
    put("b/2", 2);
    expect(invalidate((k) => k.endsWith("/2"))).toBe(1);
    expect(peek("a/1")?.data).toBe(1);
  });

  it("NOTIFIES the watchers of a dropped key", () => {
    // This is what makes an edit in one lens show up in the others with no
    // refresh — the one-task-store promise (D52/D53/D54) kept on the client.
    put("projects/tasks?a=1", 1);
    const woken = vi.fn();
    subscribe("projects/tasks?a=1", woken);
    invalidate("projects/tasks");
    expect(woken).toHaveBeenCalledTimes(1);
  });

  it("forgets an IN-FLIGHT read, so the next one does not join a stale answer", async () => {
    let release: (v: string) => void = () => {};
    const first = vi.fn(() => new Promise<string>((res) => { release = res; }));
    const pending = read("k", first);
    invalidate("k");
    const second = vi.fn(async () => "after-write");
    const fresh = read("k", second);
    release("before-write");
    await pending;
    await expect(fresh).resolves.toBe("after-write");
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe("subscribe", () => {
  it("wakes on put and stops after unsubscribe", () => {
    const cb = vi.fn();
    const off = subscribe("k", cb);
    put("k", 1);
    expect(cb).toHaveBeenCalledTimes(1);
    off();
    put("k", 2);
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("survives a watcher that unsubscribes itself while being called", () => {
    // Mutating the live Set mid-iteration silently drops the NEXT watcher.
    const second = vi.fn();
    let off: () => void = () => {};
    off = subscribe("k", () => off());
    subscribe("k", second);
    put("k", 1);
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe("eviction", () => {
  it("is bounded", () => {
    for (let i = 0; i < MAX_ENTRIES + 10; i += 1) put(`k${i}`, i);
    expect(size()).toBe(MAX_ENTRIES);
  });

  it("is least-recently-READ, not least-recently-written", () => {
    for (let i = 0; i < MAX_ENTRIES; i += 1) put(`k${i}`, i);
    peek("k0"); // the one we keep coming back to
    put("overflow", true);
    expect(peek("k0")?.data).toBe(0);
    expect(peek("k1")).toBeUndefined();
  });
});

describe("clearAll", () => {
  it("empties the store and wakes the watchers so they re-read as themselves", () => {
    put("k", 1);
    const cb = vi.fn();
    subscribe("k", cb);
    clearAll();
    expect(size()).toBe(0);
    expect(cb).toHaveBeenCalledTimes(1);
  });
});

describe("⚠️ bindIdentity — the tenancy guard", () => {
  it("EMPTIES the cache when the member changes", () => {
    // Two members, one browser. Without this the second one's first paint is
    // the first one's rows, served from memory below the gateway, where no
    // amount of FORCE ROW LEVEL SECURITY can reach them.
    bindIdentity("first@example.com");
    put("projects/tree", { rows: ["first member's projects"] });
    expect(bindIdentity("second@example.com")).toBe(true);
    expect(peek("projects/tree")).toBeUndefined();
    expect(size()).toBe(0);
  });

  it("keeps the cache when the SAME member is re-bound on every render", () => {
    bindIdentity("same@example.com");
    put("projects/tree", 1);
    expect(bindIdentity("same@example.com")).toBe(false);
    expect(peek("projects/tree")?.data).toBe(1);
  });

  it("treats signing OUT as a change", () => {
    bindIdentity("someone@example.com");
    put("projects/tree", 1);
    expect(bindIdentity(null)).toBe(true);
    expect(size()).toBe(0);
  });

  it("reports who it is holding rows for", () => {
    bindIdentity("who@example.com");
    expect(identity()).toBe("who@example.com");
  });
});

describe("⚠️ the storage fence", () => {
  it("touches NO persistent storage API", () => {
    // Memory only, forever. This map holds one signed-in member's tenant rows.
    // Persisting it leaves that data on the device for whoever opens the
    // browser next — one member reading another's rows, somewhere no
    // server-side row-level security can reach.
    const src = readFileSync(
      fileURLToPath(new URL("./dataCache.ts", import.meta.url)),
      "utf-8"
    );
    const code = src.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "");
    for (const api of ["localStorage", "sessionStorage", "indexedDB", "document.cookie"]) {
      expect(code).not.toContain(api);
    }
  });
});
