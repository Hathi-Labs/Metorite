import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isSafeColor } from "./css";
import { bindAppearanceScope } from "./scope";
import { MODE_STORAGE_KEY, STORAGE_KEYS, __newAppearanceTabForTests, onAppearanceStorage, scopedKey } from "./storage";
import { useAppearanceStore } from "./store";
import {
  APPEARANCE_URL,
  WRITE_DELAY_MS,
  __resetAppearanceSyncForTests,
  noteMemberMode,
  parseServerAppearance,
  startAppearanceSync,
} from "./syncAppearance";

/**
 * PKG-20 (`package_host.md` §9.5): the appearance follows the member to a
 * second device. The browser copy stays the first paint. The page reads the
 * server copy once, applies a field that differs, and a member change in the
 * page wins. Only a member change writes the server copy.
 *
 * Mutation: drop the `touched` check in `readAndApply`, and "a member change
 * before the read lands" fails. Let the apply use the origin "member", and
 * "no PUT after an apply" and "no write loop" fail.
 */

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

const g = globalThis as { localStorage?: Storage; window?: unknown };
let ls: Storage;

const A = { email: "a@one.test", org: "org-1" };
const B = { email: "b@two.test", org: "org-2" };
const SCOPE_A = "a@one.test|org-1";

const ROSE = "hsl(347 77% 50%)";
const STAMP = "2026-11-07T10:00:00.000Z";

type Call = { url: string; method: string; body: unknown };
let calls: Call[];

/** Stub `fetch`: the GET answers `reply()`, a PUT answers 200. */
function stubFetch(reply: () => Promise<Response> | Response) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url: String(input), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (method === "PUT") return new Response("{}", { status: 200 });
      return reply();
    }),
  );
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const puts = () => calls.filter((c) => c.method === "PUT");
const gets = () => calls.filter((c) => c.method === "GET");

/** A new page: storage stays, the module state of the tab and the sync goes. */
function newPage(): void {
  __newAppearanceTabForTests();
  __resetAppearanceSyncForTests();
  useAppearanceStore.setState({
    userDensity: null,
    accent: null,
    orgDensity: "default",
    allowUserOverride: true,
    hydrated: false,
  });
}

/** The page binds account A and the store reads its scope, as ThemeProvider does. */
function bindA(): void {
  bindAppearanceScope(A.email, A.org);
  useAppearanceStore.getState().rehydrate();
}

beforeEach(() => {
  ls = memoryStorage();
  g.localStorage = ls;
  g.window = globalThis;
  vi.useFakeTimers();
  newPage();
});
afterEach(() => {
  vi.useRealTimers();
  delete g.localStorage;
  delete g.window;
  vi.unstubAllGlobals();
  __resetAppearanceSyncForTests();
});

describe("the read of the server copy", () => {
  it("applies each field that differs from the browser copy", async () => {
    ls.setItem(scopedKey(MODE_STORAGE_KEY, SCOPE_A), "dark");
    ls.setItem(scopedKey(STORAGE_KEYS.density, SCOPE_A), "default");
    bindA();
    stubFetch(() => json({ mode: "light", density: "compact", accent: ROSE, updated_at: STAMP }));
    const setMode = vi.fn();
    await startAppearanceSync(A.email, A.org, { setMode });
    expect(gets()).toHaveLength(1);
    expect(gets()[0].url).toBe(APPEARANCE_URL);
    expect(setMode).toHaveBeenCalledWith("light");
    const s = useAppearanceStore.getState();
    expect([s.userDensity, s.accent]).toEqual(["compact", ROSE]);
    // The browser copy of the bound scope takes the values: the next load paints them.
    expect(ls.getItem(scopedKey(STORAGE_KEYS.density, SCOPE_A))).toBe("compact");
    expect(ls.getItem(scopedKey(STORAGE_KEYS.accent, SCOPE_A))).toBe(ROSE);
  });

  it("applies nothing when the server copy equals the browser copy", async () => {
    ls.setItem(scopedKey(MODE_STORAGE_KEY, SCOPE_A), "light");
    ls.setItem(scopedKey(STORAGE_KEYS.density, SCOPE_A), "compact");
    ls.setItem(scopedKey(STORAGE_KEYS.accent, SCOPE_A), ROSE);
    bindA();
    stubFetch(() => json({ mode: "light", density: "compact", accent: ROSE, updated_at: STAMP }));
    const setMode = vi.fn();
    const setAccent = vi.spyOn(useAppearanceStore.getState(), "setAccent");
    await startAppearanceSync(A.email, A.org, { setMode });
    expect(setMode).not.toHaveBeenCalled();
    expect(setAccent).not.toHaveBeenCalled();
  });

  it("reads once for each scope", async () => {
    bindA();
    stubFetch(() => json({ mode: null, density: null, accent: null, updated_at: null }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    expect(gets()).toHaveLength(1);
  });

  it("does not apply after a member change in this page", async () => {
    ls.setItem(scopedKey(MODE_STORAGE_KEY, SCOPE_A), "dark");
    bindA();
    let answer!: (r: Response) => void;
    stubFetch(() => new Promise<Response>((resolve) => (answer = resolve)));
    const setMode = vi.fn();
    const read = startAppearanceSync(A.email, A.org, { setMode });
    // The member picks an accent before the read lands.
    useAppearanceStore.getState().setAccent("#0ea5e9");
    answer(json({ mode: "light", density: "compact", accent: ROSE, updated_at: STAMP }));
    await read;
    expect(setMode).not.toHaveBeenCalled();
    expect(useAppearanceStore.getState().accent).toBe("#0ea5e9");
    expect(useAppearanceStore.getState().userDensity).toBeNull();
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()).toHaveLength(1);
    expect(puts()[0].body).toEqual({ mode: "dark", density: null, accent: "#0ea5e9" });
  });

  it("changes nothing when the read fails", async () => {
    ls.setItem(scopedKey(STORAGE_KEYS.accent, SCOPE_A), "#0ea5e9");
    bindA();
    const setMode = vi.fn();
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    await startAppearanceSync(A.email, A.org, { setMode });
    stubFetch(() => json({ detail: "unavailable" }, 503));
    newPage();
    bindA();
    await startAppearanceSync(A.email, A.org, { setMode });
    expect(setMode).not.toHaveBeenCalled();
    expect(useAppearanceStore.getState().accent).toBe("#0ea5e9");
    vi.advanceTimersByTime(WRITE_DELAY_MS * 4);
    expect(puts()).toHaveLength(0);
  });

  it("reads a 404 as off: no change, no retry, and no later PUT", async () => {
    ls.setItem(scopedKey(STORAGE_KEYS.accent, SCOPE_A), "#0ea5e9");
    bindA();
    const setMode = vi.fn();
    stubFetch(() => json({ detail: "Not found" }, 404));
    await startAppearanceSync(A.email, A.org, { setMode });
    await startAppearanceSync(A.email, A.org, { setMode });
    expect(gets()).toHaveLength(1);
    expect(setMode).not.toHaveBeenCalled();
    expect(useAppearanceStore.getState().accent).toBe("#0ea5e9");
    useAppearanceStore.getState().setAccent(ROSE);
    vi.advanceTimersByTime(WRITE_DELAY_MS * 4);
    expect(puts()).toHaveLength(0);
    // The browser copy still holds the member's choice.
    expect(useAppearanceStore.getState().accent).toBe(ROSE);
  });

  it("still syncs when the read is 200", async () => {
    bindA();
    stubFetch(() => json({ mode: "dark", density: null, accent: ROSE, updated_at: STAMP }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    expect(useAppearanceStore.getState().accent).toBe(ROSE);
    useAppearanceStore.getState().setUserDensity("compact");
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()).toHaveLength(1);
  });

  it("changes nothing for an all-null copy, so nothing flashes", async () => {
    ls.setItem(scopedKey(MODE_STORAGE_KEY, SCOPE_A), "light");
    ls.setItem(scopedKey(STORAGE_KEYS.density, SCOPE_A), "compact");
    ls.setItem(scopedKey(STORAGE_KEYS.accent, SCOPE_A), ROSE);
    bindA();
    const before = { ...useAppearanceStore.getState() };
    const setState = vi.spyOn(useAppearanceStore, "setState");
    stubFetch(() => json({ mode: null, density: null, accent: null, updated_at: null }));
    const setMode = vi.fn();
    await startAppearanceSync(A.email, A.org, { setMode });
    expect(setMode).not.toHaveBeenCalled();
    expect(setState).not.toHaveBeenCalled();
    const after = useAppearanceStore.getState();
    expect([after.userDensity, after.accent]).toEqual([before.userDensity, before.accent]);
    expect(puts()).toHaveLength(0);
  });

  it("skips the density of an org that locks it, and still sends the member's choice", async () => {
    ls.setItem(scopedKey(STORAGE_KEYS.density, SCOPE_A), "comfortable");
    bindA();
    useAppearanceStore.getState().setOrgDefaults({ density: "default", allowUserOverride: false });
    stubFetch(() => json({ mode: "dark", density: "compact", accent: ROSE, updated_at: STAMP }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    expect(useAppearanceStore.getState().userDensity).toBe("comfortable");
    expect(useAppearanceStore.getState().accent).toBe(ROSE);
    noteMemberMode("light");
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()[0].body).toEqual({ mode: "light", density: "comfortable", accent: ROSE });
  });

  it("does nothing before the bind, or for a scope this tab does not show", async () => {
    stubFetch(() => json({ mode: "light", density: "compact", accent: ROSE, updated_at: STAMP }));
    const setMode = vi.fn();
    // No full scope: an email with no org and none remembered.
    await startAppearanceSync(A.email, null, { setMode });
    bindA();
    // B is not the scope this tab shows.
    await startAppearanceSync(B.email, B.org, { setMode });
    expect(calls).toHaveLength(0);
    expect(setMode).not.toHaveBeenCalled();
  });
});

describe("the write of the server copy", () => {
  it("sends no PUT after an apply", async () => {
    bindA();
    stubFetch(() => json({ mode: "light", density: "compact", accent: ROSE, updated_at: STAMP }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    vi.advanceTimersByTime(WRITE_DELAY_MS * 10);
    expect(puts()).toHaveLength(0);
  });

  it("sends the whole record once, 500 ms after the last member change", async () => {
    bindA();
    stubFetch(() => json({ mode: null, density: null, accent: null, updated_at: null }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    const store = useAppearanceStore.getState();
    store.setUserDensity("compact");
    vi.advanceTimersByTime(WRITE_DELAY_MS - 100);
    store.setAccent(ROSE);
    vi.advanceTimersByTime(WRITE_DELAY_MS - 100);
    noteMemberMode("light");
    expect(puts()).toHaveLength(0);
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()).toHaveLength(1);
    expect(puts()[0]).toMatchObject({ url: APPEARANCE_URL, body: { mode: "light", density: "compact", accent: ROSE } });
  });

  it("sends the mode the tab shows after another tab of the account changed it", async () => {
    bindA();
    stubFetch(() => json({ mode: null, density: null, accent: null, updated_at: null }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    noteMemberMode("dark");
    ls.setItem(scopedKey(MODE_STORAGE_KEY, SCOPE_A), "light");
    ls.setItem(MODE_STORAGE_KEY, "light");
    onAppearanceStorage({ key: MODE_STORAGE_KEY, newValue: "light" } as StorageEvent);
    useAppearanceStore.getState().setAccent("#0ea5e9");
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()[0]).toMatchObject({ body: { mode: "light" } });
  });

  it("writes the browser copy first, and a failed PUT is silent", async () => {
    bindA();
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_i: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(_i), method: init?.method ?? "GET", body: undefined });
        throw new TypeError("fetch failed");
      }),
    );
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    useAppearanceStore.getState().setAccent(ROSE);
    expect(ls.getItem(scopedKey(STORAGE_KEYS.accent, SCOPE_A))).toBe(ROSE);
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    await Promise.resolve();
    expect(puts()).toHaveLength(1);
    expect(useAppearanceStore.getState().accent).toBe(ROSE);
  });

  it("holds a change made before the bind, and sends it to the bound scope", async () => {
    stubFetch(() => json({ mode: "light", density: "compact", accent: ROSE, updated_at: STAMP }));
    useAppearanceStore.getState().setUserDensity("comfortable");
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()).toHaveLength(0);
    bindA();
    const setMode = vi.fn();
    await startAppearanceSync(A.email, A.org, { setMode });
    expect(puts()).toHaveLength(1);
    expect(puts()[0].body).toMatchObject({ density: "comfortable" });
    // The member changed it in this page, so the read applies nothing.
    expect(setMode).not.toHaveBeenCalled();
  });

  it("a passive tab writes nothing", async () => {
    bindA();
    stubFetch(() => json({ mode: null, density: null, accent: null, updated_at: null }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    // Another tab moved the pointer to account B.
    onAppearanceStorage({ key: "cc-appearance-scope", newValue: "b@two.test|org-2" });
    useAppearanceStore.getState().setAccent(ROSE);
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()).toHaveLength(0);
  });

  it("makes no write loop between two browsers", async () => {
    // A server that keeps the last PUT, as the gateway does.
    let server: Record<string, unknown> = { mode: null, density: null, accent: null, updated_at: null };
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ url: String(input), method, body });
        if (method === "PUT") server = { ...body, updated_at: STAMP };
        return json(server);
      }),
    );

    // Browser 1 sets Light, Compact and Rose.
    bindA();
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    noteMemberMode("light");
    useAppearanceStore.getState().setUserDensity("compact");
    useAppearanceStore.getState().setAccent(ROSE);
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()).toHaveLength(1);

    // Browser 2, with empty storage, opens for the same member and org.
    ls = memoryStorage();
    g.localStorage = ls;
    newPage();
    calls = [];
    bindA();
    const setMode = vi.fn();
    await startAppearanceSync(A.email, A.org, { setMode });
    expect(setMode).toHaveBeenCalledWith("light");
    expect(useAppearanceStore.getState().userDensity).toBe("compact");
    expect(useAppearanceStore.getState().accent).toBe(ROSE);
    // The apply sends nothing back, and the effect running again reads nothing.
    vi.advanceTimersByTime(WRITE_DELAY_MS * 10);
    await startAppearanceSync(A.email, A.org, { setMode });
    vi.advanceTimersByTime(WRITE_DELAY_MS * 10);
    expect(calls.map((c) => c.method)).toEqual(["GET"]);
  });

  it("drops a pending change of one account when the page binds another", async () => {
    bindA();
    stubFetch(() => json({ mode: null, density: null, accent: null, updated_at: null }));
    await startAppearanceSync(A.email, A.org, { setMode: vi.fn() });
    useAppearanceStore.getState().setAccent(ROSE);
    bindAppearanceScope(B.email, B.org);
    await startAppearanceSync(B.email, B.org, { setMode: vi.fn() });
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(puts()).toHaveLength(0);
    expect(gets()).toHaveLength(2);
  });
});

describe("the answer of the route", () => {
  it("reads an all-null copy as no copy", () => {
    expect(parseServerAppearance({ mode: null, density: null, accent: null, updated_at: null })).toBeNull();
    expect(parseServerAppearance(null)).toBeNull();
    expect(parseServerAppearance("x")).toBeNull();
  });

  it("drops a field that is not valid and keeps the rest", () => {
    expect(parseServerAppearance({ mode: "system", density: "huge", accent: "url(x)", updated_at: STAMP })).toEqual({
      mode: null,
      density: null,
      accent: null,
      updated_at: STAMP,
    });
  });
});

describe("the shared accent samples (tests/fixtures/accent_samples.json)", () => {
  // `tests/unit/test_auth_me_appearance.py` reads the same file against the
  // gateway's port of the rule. The server refuses what the client refuses.
  const samples = JSON.parse(
    readFileSync(fileURLToPath(new URL("../../../../../tests/fixtures/accent_samples.json", import.meta.url)), "utf-8"),
  ) as { accepted: string[]; refused: string[] };

  it("holds both halves", () => {
    expect(samples.accepted.length).toBeGreaterThanOrEqual(20);
    expect(samples.refused.length).toBeGreaterThanOrEqual(20);
  });

  it("isSafeColor accepts every accepted sample", () => {
    expect(samples.accepted.filter((s) => !isSafeColor(s))).toEqual([]);
  });

  it("isSafeColor refuses every refused sample", () => {
    expect(samples.refused.filter((s) => isSafeColor(s))).toEqual([]);
  });
});
