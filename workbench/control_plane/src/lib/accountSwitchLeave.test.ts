/**
 * Every way a page leaves its account calls `leaveAccount` just before it
 * navigates, and none of them clears the read cache (WS-58 MD-2b fix round 2).
 *
 * Round 1 called `bindIdentity(null)` there. That emptied the cache, which
 * woke every mounted reader of the leaving page, and each read went out under
 * the NEW cookie before the reload. Its `onClear` callbacks also closed open
 * panes. So a switch changed what every member saw, flag or no flag.
 *
 * The cache and the signal are module mocks with spies, so a call through any
 * import path counts, not only a name in the source.
 *
 * Mutations, run by hand (2026-10-14), each fails here:
 *   - put `bindIdentity(null)` back before `go` in `switchTo`;
 *   - drop `leaveAccount()` from `reloadForAnnounce`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const order: string[] = [];

vi.mock("next-auth/react", () => ({ signOut: vi.fn(async () => undefined) }));
vi.mock("@/lib/accountLeave", () => ({
  leaveAccount: vi.fn(() => order.push("leave")),
  onAccountLeave: vi.fn(() => () => {}),
}));
vi.mock("@/lib/dataCache", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/dataCache")>();
  return {
    ...actual,
    bindIdentity: vi.fn(() => order.push("bindIdentity")),
    clearAll: vi.fn(() => order.push("clearAll")),
    invalidate: vi.fn(() => order.push("invalidate")),
  };
});

import { leaveAccount } from "@/lib/accountLeave";
import { bindIdentity, clearAll, invalidate } from "@/lib/dataCache";

import { reloadForAnnounce, resetLeaving, signOutThis, switchTo } from "./accountSwitch";

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

const g = globalThis as { localStorage?: Storage; sessionStorage?: Storage; window?: unknown };

beforeEach(() => {
  order.length = 0;
  g.localStorage = memoryStorage();
  g.sessionStorage = memoryStorage();
  g.window = globalThis;
  vi.mocked(leaveAccount).mockClear();
});
afterEach(() => {
  resetLeaving();
  delete g.localStorage;
  delete g.sessionStorage;
  delete g.window;
  vi.unstubAllGlobals();
});

/** The cache is never touched by a leave. */
function expectNoCacheCall() {
  expect(bindIdentity).not.toHaveBeenCalled();
  expect(clearAll).not.toHaveBeenCalled();
  expect(invalidate).not.toHaveBeenCalled();
}

/** A server that answers each account act in turn. */
function server(...replies: (Response | "throw")[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const next = replies.shift() ?? Response.json({ ok: true });
      if (next === "throw") throw new TypeError("offline");
      return next;
    }),
  );
}

describe("each way a page leaves its account", () => {
  it("switchTo leaves the account just before it loads /", async () => {
    server(Response.json({ ok: true, email: "b@two.test" }));
    const go = vi.fn(() => order.push("go"));
    expect(await switchTo(1, go)).toBe(true);
    expect(order).toEqual(["leave", "go"]);
    expectNoCacheCall();
  });

  it("a refused switch does not leave", async () => {
    server(new Response("{}", { status: 409 }));
    expect(await switchTo(1, vi.fn())).toBe(false);
    expect(leaveAccount).not.toHaveBeenCalled();
  });

  it("signOutThis leaves the account just before it loads /", async () => {
    server(Response.json({ ok: true, email: "b@two.test" }), Response.json({ ok: true }));
    const go = vi.fn(() => order.push("go"));
    expect(await signOutThis("a@one.test", { slot: 2 }, go, vi.fn(async () => {}))).toBe("one");
    expect(order).toEqual(["leave", "go"]);
    expectNoCacheCall();
  });

  it("signOutThis leaves the account on its other exit too: a switched cookie and a fallback that throws", async () => {
    server(Response.json({ ok: true, email: "b@two.test" }), new Response("{}", { status: 500 }), "throw");
    const go = vi.fn(() => order.push("go"));
    const all = vi.fn(async () => {
      throw new Error("sign-out failed");
    });
    expect(await signOutThis("a@one.test", { slot: 2 }, go, all)).toBe("reload");
    expect(order).toEqual(["leave", "go"]);
    expectNoCacheCall();
  });

  it("a tab that hears another account leaves its account, then loads /", () => {
    const assign = vi.fn(() => order.push("assign"));
    vi.stubGlobal("location", { assign });
    reloadForAnnounce("a@one.test", "b@two.test");
    expect(order).toEqual(["leave", "assign"]);
    expect(assign).toHaveBeenCalledWith("/");
    expectNoCacheCall();
    // Its own account, or no account, changes nothing.
    order.length = 0;
    reloadForAnnounce("a@one.test", "A@one.test");
    reloadForAnnounce("a@one.test", "");
    expect(order).toEqual([]);
  });
});
