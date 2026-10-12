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
  signOutThis,
  switchTo,
  SIGN_OUT_ALL_NOTICE,
  clearSignOutNotice,
  peekSignOutNotice,
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

describe("org-aware links: where a switch and a sign-in land", () => {
  // R7 fence `switch-target-is-local`. A link names the path, so a switch
  // that went to any path it was handed would be an open redirect.
  it("lands on a same-origin target, and still clears the org-scoped keys", async () => {
    const go = vi.fn();
    localStorage.setItem("cc-org-branding-v1", "logo of org A");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true })));
    expect(await switchTo(1, go, "/projects?task=1")).toBe(true);
    expect(go).toHaveBeenCalledWith("/projects?task=1");
    expect(localStorage.getItem("cc-org-branding-v1")).toBeNull();
  });

  it.each(["//evil.example", "https://x", "/\\evil.example", "javascript:alert(1)", ""])(
    "lands on / for the target %j",
    async (target) => {
      resetLeaving();
      const go = vi.fn();
      vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true })));
      expect(await switchTo(1, go, target)).toBe(true);
      expect(go).toHaveBeenCalledWith("/");
    },
  );

  it("Add another account forwards a same-origin callbackUrl, and drops any other", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true })));
    const go = vi.fn();
    const link = "/projects?task=t1&org=3f2a6c1e-9b7d-4e2a-8c1f-0a1b2c3d4e5f";
    expect(await addAccount(go, link)).toBe(true);
    const url = new URL(go.mock.calls[0][0] as string, "https://app.example");
    expect(url.pathname).toBe("/signin");
    expect(url.searchParams.get("add")).toBe("1");
    expect(url.searchParams.get("callbackUrl")).toBe(link);

    const go2 = vi.fn();
    await addAccount(go2, "//evil.example");
    expect(go2).toHaveBeenCalledWith("/signin?add=1");
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

/**
 * Sign out of ONE account and keep the others (owner, 2026-10-12, the
 * top-right account menu). It is built from the two server actions that
 * exist: switch to the next account, then remove the slot the leaving
 * account went into.
 */
describe("sign out of this account only", () => {
  /** A fetch that answers each action in turn from `plan`, and records it. */
  function server(plan: Record<string, (Response | "throw")[]>) {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${url} ${init?.body ?? ""}`);
        const action = url.split("/").pop() ?? "";
        const next = plan[action]?.shift() ?? Response.json({ ok: true });
        if (next === "throw") throw new TypeError("network down");
        return next;
      }),
    );
    return calls;
  }
  const ok = () => Response.json({ ok: true, email: "b@two.test" });
  const status = (n: number) => new Response("{}", { status: n });
  const SWITCH = '/api/accounts/switch {"slot":2}';
  const REMOVE = '/api/accounts/remove {"slot":2}';

  beforeEach(() => {
    (globalThis as { sessionStorage?: Storage }).sessionStorage = memoryStorage();
    localStorage.setItem("cc-chat::a@one.test|org1::sessions", "1");
    localStorage.setItem("cc-chat::b@two.test|org2::sessions", "1");
  });
  afterEach(() => {
    delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
  });

  it("switches to the next account, removes the SAME slot, clears only the leaving account, then loads /", async () => {
    localStorage.setItem("cc-org-branding-v1", "logo of org A");
    const calls = server({ switch: [ok()] });
    const go = vi.fn();
    const all = vi.fn(async () => {});
    expect(await signOutThis("a@one.test", { slot: 2 }, go, all)).toBe("one");
    expect(calls).toEqual([SWITCH, REMOVE]);
    expect(localStorage.getItem("cc-chat::a@one.test|org1::sessions")).toBeNull();
    expect(localStorage.getItem("cc-chat::b@two.test|org2::sessions")).toBe("1");
    expect(localStorage.getItem("cc-org-branding-v1")).toBeNull();
    expect(go).toHaveBeenCalledWith("/");
    // Not a sign-out of the browser: the next account stays signed in.
    expect(all).not.toHaveBeenCalled();
    expect(peekSignOutNotice()).toBeNull();
    // This tab is leaving, so its own channel never starts a second load.
    expect(shouldReloadFor("a@one.test", "b@two.test")).toBe(false);
  });

  it("tells the other tabs the new account BEFORE the remove", async () => {
    const order: string[] = [];
    class FakeChannel {
      postMessage(m: { email: string }) {
        order.push(`announce ${m.email}`);
      }
      close() {}
    }
    vi.stubGlobal("BroadcastChannel", FakeChannel);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        order.push(url);
        return ok();
      }),
    );
    await signOutThis("a@one.test", { slot: 2 }, vi.fn(), vi.fn(async () => {}));
    expect(order).toEqual(["/api/accounts/switch", "announce b@two.test", "/api/accounts/remove"]);
  });

  // ⚠️ Fails CLOSED (review of 2026-10-12, P1). Each failure below ends in a
  // sign-out of every account, never on `/` with the leaving slot held.
  it.each([
    ["the switch is refused (409: another tab removed it)", { switch: [status(409)] }, [SWITCH]],
    ["the switch fails (500)", { switch: [status(500)] }, [SWITCH]],
    ["the switch throws (network)", { switch: ["throw" as const] }, [SWITCH]],
    ["the remove fails twice", { switch: [ok()], remove: [status(500), status(500)] }, [SWITCH, REMOVE, REMOVE]],
    ["the remove throws twice", { switch: [ok()], remove: ["throw" as const, "throw" as const] }, [SWITCH, REMOVE, REMOVE]],
  ])("%s: signs out of every account, with the notice", async (_name, plan, expected) => {
    const calls = server(plan as Record<string, (Response | "throw")[]>);
    const go = vi.fn();
    const all = vi.fn(async () => {});
    expect(await signOutThis("a@one.test", { slot: 2 }, go, all)).toBe("all");
    expect(calls).toEqual(expected);
    expect(all).toHaveBeenCalledTimes(1);
    expect(go).not.toHaveBeenCalled();
    expect(peekSignOutNotice()).toBe(SIGN_OUT_ALL_NOTICE);
    // A read keeps it, so a remount still shows it. A clear ends it.
    expect(peekSignOutNotice()).toBe(SIGN_OUT_ALL_NOTICE);
    clearSignOutNotice();
    expect(peekSignOutNotice()).toBeNull();
  });

  it("a remove that fails once and then works is a single sign-out", async () => {
    const calls = server({ switch: [ok()], remove: [status(500), Response.json({ ok: true })] });
    const go = vi.fn();
    const all = vi.fn(async () => {});
    expect(await signOutThis("a@one.test", { slot: 2 }, go, all)).toBe("one");
    expect(calls).toEqual([SWITCH, REMOVE, REMOVE]);
    expect(all).not.toHaveBeenCalled();
    expect(go).toHaveBeenCalledWith("/");
    expect(localStorage.getItem("cc-chat::a@one.test|org1::sessions")).toBeNull();
  });

  it("the default fallback is signOutAll, a real sign-out of the browser", async () => {
    const { signOut } = await import("next-auth/react");
    vi.mocked(signOut).mockClear();
    server({ switch: [status(409)], "signout-all": [Response.json({ ok: true, emails: ["a@one.test"] })] });
    expect(await signOutThis("a@one.test", { slot: 2 }, vi.fn())).toBe("all");
    expect(signOut).toHaveBeenCalledWith({ callbackUrl: "/signin" });
  });

  it("if the fallback itself throws, this tab hears the other tabs again", async () => {
    server({ switch: [status(409)] });
    await expect(
      signOutThis("a@one.test", { slot: 2 }, vi.fn(), async () => {
        throw new Error("sign-out failed");
      }),
    ).rejects.toThrow("sign-out failed");
    expect(shouldReloadFor("a@one.test", "b@two.test")).toBe(true);
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
