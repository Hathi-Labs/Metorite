import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next-auth/react", () => ({ signOut: vi.fn(async () => undefined) }));

import {
  ORG_SCOPED_KEYS,
  addAccount,
  clearOrgScopedStorage,
  resetLeaving,
  shouldReloadFor,
  signOutAll,
  switchTo,
} from "./accountSwitch";
import { clearAccountNamespaces } from "./sessions";

/**
 * MT-1k slice A2, rules 3 and 4: what a switch and a sign-out of all accounts
 * leave behind in this browser.
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
const SRC = path.join(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf-8");

beforeEach(() => {
  g.localStorage = memoryStorage();
  g.window = globalThis;
});
afterEach(() => {
  resetLeaving();
  delete g.localStorage;
  delete g.window;
  vi.unstubAllGlobals();
});

describe("rule 3: the org-scoped keys", () => {
  it.each([
    ["cc-org-branding-v1", "lib/orgBranding.ts"],
    ["cc-density-org", "lib/theme/storage.ts"],
    ["cc.email.selectedAccountId", "app/email/lib/emailStore.ts"],
  ])("%s is still the key its owner writes (%s)", (key, owner) => {
    expect(ORG_SCOPED_KEYS).toContain(key);
    expect(read(owner)).toContain(`"${key}"`);
  });

  it("clears every org-scoped key and nothing else", () => {
    for (const k of ORG_SCOPED_KEYS) localStorage.setItem(k, "x");
    localStorage.setItem("cc-sidebar-collapsed", "1");
    clearOrgScopedStorage();
    for (const k of ORG_SCOPED_KEYS) expect(localStorage.getItem(k)).toBeNull();
    expect(localStorage.getItem("cc-sidebar-collapsed")).toBe("1");
  });

  it("a switch clears the keys, then reloads at / — and does neither when refused", async () => {
    const go = vi.fn();
    localStorage.setItem("cc-org-branding-v1", "logo of org A");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 409 })));
    expect(await switchTo(1, go)).toBe(false);
    expect(go).not.toHaveBeenCalled();
    expect(localStorage.getItem("cc-org-branding-v1")).toBe("logo of org A");

    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true })));
    expect(await switchTo(1, go)).toBe(true);
    expect(go).toHaveBeenCalledWith("/");
    expect(localStorage.getItem("cc-org-branding-v1")).toBeNull();
  });

  it("a switch tells the other tabs the new account before it reloads", async () => {
    const posted: unknown[] = [];
    class FakeChannel {
      constructor(public name: string) {}
      postMessage(m: unknown) {
        posted.push([this.name, m]);
      }
      close() {}
    }
    vi.stubGlobal("BroadcastChannel", FakeChannel);
    const go = vi.fn(() => expect(posted).toHaveLength(1));
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true, email: "b@two.test" })));
    expect(await switchTo(1, go)).toBe(true);
    expect(posted).toEqual([["mt-active-account", { email: "b@two.test" }]]);
    expect(go).toHaveBeenCalledWith("/");
    // This tab hears its own announcement on its second channel object. It
    // must not start a second navigation, which aborts the first.
    expect(shouldReloadFor("a@one.test", "b@two.test")).toBe(false);
  });

  it("Add another account keeps this one first, then opens sign-in", async () => {
    const go = vi.fn();
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await addAccount(go)).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/accounts/stash");
    expect(go).toHaveBeenCalledWith("/signin?add=1");
  });
});

describe("rule 4: sign out of all accounts", () => {
  it("clears every account's chat namespace, in both cases, and leaves strangers' alone", async () => {
    localStorage.setItem("cc-chat::a@one.test|org1::sessions", "1");
    localStorage.setItem("cc-chat::b@two.test|org2::sessions", "1");
    localStorage.setItem("cc-chat::c@three.test|org3::sessions", "1");
    localStorage.setItem("cc-chat-last-scope", "a@one.test|org1");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ok: true, emails: ["a@one.test", "B@Two.test"] })),
    );
    await signOutAll();
    expect(localStorage.getItem("cc-chat::a@one.test|org1::sessions")).toBeNull();
    expect(localStorage.getItem("cc-chat::b@two.test|org2::sessions")).toBeNull();
    expect(localStorage.getItem("cc-chat::c@three.test|org3::sessions")).toBe("1");
    expect(localStorage.getItem("cc-chat-last-scope")).toBeNull();
    const { signOut } = await import("next-auth/react");
    expect(signOut).toHaveBeenCalledWith({ callbackUrl: "/signin" });
  });

  it("still signs out when the slot call fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    await signOutAll();
    const { signOut } = await import("next-auth/react");
    expect(signOut).toHaveBeenCalled();
  });

  it("clearAccountNamespaces uses the chat key shape that sessions.ts writes", () => {
    expect(read("lib/sessions.ts")).toContain('"cc-chat::"');
    clearAccountNamespaces([]);
  });
});

describe("other tabs follow the cookie", () => {
  it("reloads a tab that hears another account, in any case", () => {
    expect(shouldReloadFor("a@one.test", "b@two.test")).toBe(true);
    expect(shouldReloadFor("a@one.test", "A@One.test")).toBe(false);
  });

  it("ignores a tab with no account, and a message with none", () => {
    expect(shouldReloadFor(null, "b@two.test")).toBe(false);
    expect(shouldReloadFor("a@one.test", undefined)).toBe(false);
    expect(shouldReloadFor("a@one.test", "")).toBe(false);
    expect(shouldReloadFor("a@one.test", 42)).toBe(false);
  });
});
