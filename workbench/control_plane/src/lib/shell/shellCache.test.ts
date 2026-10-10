/**
 * The browser copy of the layout, and the writes around it (NS-7 round 2).
 *
 * - The copy is per account: one email in one organization.
 * - Only a layout the server confirmed is kept. A refused write never is.
 * - A sign-out clears it, through the two functions that clear the chat
 *   caches, so an email does not outlive its sign-out in a key name.
 * - A refused answer to the question stays for this page (`keep`). A
 *   refused pin goes back.
 *
 * Mutations, seen to fail first: drop `clearCachedLayouts` from
 * `clearSignedOutAccount`, and "a sign-out clears the account's layouts"
 * fails. Let a refused save set `confirmed`, and "a refused write is never
 * kept in the browser" fails.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, peek, put } from "@/lib/dataCache";
import {
  __newPageForTests,
  bindChatScope,
  chatScope,
  clearAccountNamespaces,
  clearSignedOutAccount,
  tidyChatStorage,
} from "@/lib/sessions";

import { EMPTY_SHELL, type StoredShell } from "./presets";
import { clearCachedLayouts, readCachedLayout, shellCacheKey, writeCachedLayout } from "./shellCache";
import {
  SHELL_PREFS_PATH,
  __newShellPageForTests,
  confirmedLayout,
  readShellPrefs,
  saveShellPrefs,
  shellReadEnabled,
} from "./shellPrefs";
import { cardOrderFor } from "./myDay";

class MemoryStorage {
  private map = new Map<string, string>();
  get length(): number {
    return this.map.size;
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
  keys(): string[] {
    return [...this.map.keys()];
  }
}

const A = chatScope("Priya@Acme.example", "org-acme") as string;
const A_ELSEWHERE = chatScope("priya@acme.example", "org-beta") as string;
const B = chatScope("ben@beta.example", "org-beta") as string;
const ENGINEER: StoredShell = { ...EMPTY_SHELL, preset: "engineer", answered: "answered", pins: ["/tasks"] };
const FOUNDER: StoredShell = { ...EMPTY_SHELL, preset: "founder", answered: "answered" };

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  __newPageForTests();
  __newShellPageForTests();
  clearAll();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the copy is per account", () => {
  it("keys the copy by email and organization, case-folded", () => {
    expect(shellCacheKey(A)).toBe("cc-shell-prefs:priya@acme.example|org-acme");
    expect(shellCacheKey(null)).toBeNull();
    expect(shellCacheKey("no-scope")).toBeNull();
  });

  it("reads back what it wrote, and nothing for another account", () => {
    writeCachedLayout(A, ENGINEER);
    expect(readCachedLayout(A)).toEqual(ENGINEER);
    expect(readCachedLayout(A_ELSEWHERE)).toBeUndefined();
    expect(readCachedLayout(B)).toBeUndefined();
  });

  it("reads nothing from a value that is not a layout", () => {
    storage.setItem(shellCacheKey(A)!, "{not json");
    expect(readCachedLayout(A)).toBeUndefined();
    storage.setItem(shellCacheKey(A)!, JSON.stringify({ pins: ["/tasks"] }));
    expect(readCachedLayout(A)).toBeUndefined();
  });

  it("works with storage turned off", () => {
    vi.stubGlobal("window", undefined);
    expect(() => writeCachedLayout(A, ENGINEER)).not.toThrow();
    expect(readCachedLayout(A)).toBeUndefined();
  });
});

describe("a sign-out clears the account's layouts", () => {
  it("clears every organization of one email, and keeps another account's", () => {
    writeCachedLayout(A, ENGINEER);
    writeCachedLayout(A_ELSEWHERE, FOUNDER);
    writeCachedLayout(B, FOUNDER);
    clearCachedLayouts(["Priya@Acme.example"]);
    expect(readCachedLayout(A)).toBeUndefined();
    expect(readCachedLayout(A_ELSEWHERE)).toBeUndefined();
    expect(readCachedLayout(B)).toEqual(FOUNDER);
  });

  it("clearSignedOutAccount clears the last account's layouts with its chats", () => {
    bindChatScope(A);
    tidyChatStorage(A);
    writeCachedLayout(A, ENGINEER);
    writeCachedLayout(B, FOUNDER);
    clearSignedOutAccount();
    expect(readCachedLayout(A)).toBeUndefined();
    expect(readCachedLayout(B)).toEqual(FOUNDER);
    expect(storage.keys().some((k) => k.includes("priya@acme.example"))).toBe(false);
  });

  it("clearAccountNamespaces clears every named account's layouts", () => {
    writeCachedLayout(A, ENGINEER);
    writeCachedLayout(B, FOUNDER);
    clearAccountNamespaces(["priya@acme.example", "ben@beta.example"]);
    expect(storage.keys().filter((k) => k.startsWith("cc-shell-prefs:"))).toEqual([]);
  });
});

describe("the writes", () => {
  const reply = (status: number, body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status }));

  it("a read the server answered is the confirmed layout", async () => {
    vi.stubGlobal("fetch", reply(200, ENGINEER));
    await readShellPrefs();
    expect(confirmedLayout()).toEqual(ENGINEER);
  });

  it("a refused write is never kept in the browser", async () => {
    vi.stubGlobal("fetch", reply(200, ENGINEER));
    await readShellPrefs();
    vi.stubGlobal("fetch", reply(503, { detail: "unavailable" }));
    await expect(saveShellPrefs(FOUNDER, { keep: true })).rejects.toThrow();
    expect(confirmedLayout()).toEqual(ENGINEER);
  });

  it("a refused answer stays for this page", async () => {
    put(SHELL_PREFS_PATH, { ...EMPTY_SHELL });
    vi.stubGlobal("fetch", reply(503, { detail: "unavailable" }));
    await expect(saveShellPrefs(FOUNDER, { keep: true })).rejects.toThrow();
    expect(peek<StoredShell>(SHELL_PREFS_PATH)?.data).toEqual(FOUNDER);
  });

  it("a refused pin goes back", async () => {
    put(SHELL_PREFS_PATH, ENGINEER);
    vi.stubGlobal("fetch", reply(503, { detail: "unavailable" }));
    await expect(saveShellPrefs({ ...ENGINEER, pins: ["/tasks", "/email"] })).rejects.toThrow();
    expect(peek<StoredShell>(SHELL_PREFS_PATH)?.data).toEqual(ENGINEER);
  });

  it("a saved write is the confirmed layout", async () => {
    vi.stubGlobal("fetch", reply(200, FOUNDER));
    await saveShellPrefs(FOUNDER);
    expect(confirmedLayout()).toEqual(FOUNDER);
  });
});

describe("the read starts with the access read", () => {
  const on = { navOn: true, sessionStatus: "authenticated", accessLoading: true, hasWorkspace: false };

  it("reads while access resolves, once a session exists", () => {
    expect(shellReadEnabled(on)).toBe(true);
    expect(shellReadEnabled({ ...on, sessionStatus: "loading" })).toBe(false);
  });

  it("after access, reads only for a member with a workspace", () => {
    expect(shellReadEnabled({ ...on, accessLoading: false, hasWorkspace: true })).toBe(true);
    expect(shellReadEnabled({ ...on, accessLoading: false, hasWorkspace: false })).toBe(false);
  });

  it("never reads with the shell nav off", () => {
    expect(shellReadEnabled({ ...on, navOn: false })).toBe(false);
  });
});

describe("My Day's card order", () => {
  it("keeps the page's order with the shell nav off", () => {
    expect(cardOrderFor(["needs", "today", "next"], { enabled: false, layout: { cards: ["next"] } })).toEqual([
      "needs",
      "today",
      "next",
    ]);
  });

  it("takes the member's order with it on", () => {
    expect(
      cardOrderFor(["needs", "today", "next"], { enabled: true, layout: { cards: ["next", "today", "needs"] } }),
    ).toEqual(["next", "today", "needs"]);
  });
});
