import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next-auth/react", () => ({ signOut: vi.fn(async () => undefined) }));

import { resetLeaving, signOutAll, switchTo } from "@/lib/accountSwitch";
import { themeBootScript } from "./boot";
import {
  appearanceScopeFor,
  bindAppearanceScope,
  clearAppearanceForAccounts,
  clearSignedOutAppearance,
  pointAppearanceAt,
  reconcileAppearanceScope,
} from "./scope";
import {
  APPEARANCE_SCOPE_KEY,
  __newAppearanceTabForTests,
  isAppearancePassive,
  onAppearanceStorage,
  tabAppearanceScope,
  themeStorage,
} from "./storage";
import { useAppearanceStore } from "./store";

/**
 * Appearance is per account on this device (owner bug, 2026-10-11): "setting
 * the appearance in one organization carries over to others." These are the
 * fences `scope.ts` names.
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
const SCOPE_B = "b@two.test|org-2";

beforeEach(() => {
  ls = memoryStorage();
  g.localStorage = ls;
  g.window = globalThis;
  __newAppearanceTabForTests();
});
afterEach(() => {
  resetLeaving();
  delete g.localStorage;
  delete g.window;
  vi.unstubAllGlobals();
});

/** Run the boot script against this storage and a fake `<html>`. */
function boot(): Record<string, string> {
  const props: Record<string, string> = {};
  const documentElement = { style: { setProperty: (p: string, v: string) => void (props[p] = v) } };
  new Function("window", "document", themeBootScript())({ localStorage: ls }, { documentElement });
  return props;
}

describe("(a) two accounts keep their own appearance", () => {
  it("accent, density and colour mode stay apart, and come back", () => {
    const store = useAppearanceStore.getState();
    bindAppearanceScope(A.email, A.org);
    store.rehydrate();
    store.setAccent("rgb(255, 0, 0)");
    store.setUserDensity("compact");
    themeStorage.mirrorMode("light");

    expect(bindAppearanceScope(B.email, B.org)).toBe(true);
    store.rehydrate();
    expect(useAppearanceStore.getState().accent).toBeNull();
    expect(useAppearanceStore.getState().userDensity).toBeNull();
    expect(themeStorage.getMode()).toBeNull();
    store.setAccent("rgb(0, 0, 255)");

    bindAppearanceScope(A.email, A.org);
    store.rehydrate();
    expect(useAppearanceStore.getState().accent).toBe("rgb(255, 0, 0)");
    expect(useAppearanceStore.getState().userDensity).toBe("compact");
    expect(themeStorage.getMode()).toBe("light");
    expect(ls.getItem(`cc-accent:${SCOPE_B}`)).toBe("rgb(0, 0, 255)");
  });

  it("the scope is the chat scope format, case-folded, and no email names nobody", () => {
    expect(appearanceScopeFor(" A@One.Test ", "org-1")).toBe(SCOPE_A);
    expect(appearanceScopeFor(null, "org-1")).toBeNull();
    expect(appearanceScopeFor("anonymous", "")).toBeNull();
  });
});

describe("(b) the boot script paints the active account", () => {
  beforeEach(() => {
    ls.setItem(`cc-accent:${SCOPE_A}`, "rgb(255, 0, 0)");
    ls.setItem(`cc-accent-ink:${SCOPE_A}`, "#ffffff");
    ls.setItem(`cc-density:${SCOPE_A}`, "compact");
    ls.setItem(`theme:${SCOPE_A}`, "light");
    ls.setItem(`cc-accent:${SCOPE_B}`, "rgb(0, 0, 255)");
    ls.setItem(`cc-density-org:${SCOPE_B}`, "comfortable");
    ls.setItem("theme", "light");
  });

  it("scope A gets A's accent, ink, density and mode", () => {
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_A);
    const props = boot();
    expect(props["--primary"]).toBe("rgb(255, 0, 0)");
    expect(props["--primary-foreground"]).toBe("#ffffff");
    expect(props["--ui-scale"]).toBe("0.92");
    expect(ls.getItem("theme")).toBe("light");
  });

  it("scope B gets B's accent and its org density, and no mode of A", () => {
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_B);
    const props = boot();
    expect(props["--primary"]).toBe("rgb(0, 0, 255)");
    expect(props["--primary-foreground"]).toBeUndefined();
    expect(props["--ui-scale"]).toBe("1.08");
    // B never chose a mode, so next-themes falls back to its default.
    expect(ls.getItem("theme")).toBeNull();
  });

  it("with no pointer it reads the bare keys, as before", () => {
    ls.setItem("cc-accent", "rgb(0, 128, 0)");
    const props = boot();
    expect(props["--primary"]).toBe("rgb(0, 128, 0)");
    expect(props["--ui-scale"]).toBeUndefined();
    expect(ls.getItem("theme")).toBe("light");
  });

  it("does not throw when storage is blocked", () => {
    const blocked = { getItem: () => { throw new Error("blocked"); } };
    expect(() =>
      new Function("window", "document", themeBootScript())({ localStorage: blocked }, { documentElement: {} }),
    ).not.toThrow();
  });
});

describe("(c) legacy values move once, into the first account only", () => {
  it("the first bind adopts them, and a second account inherits nothing", () => {
    ls.setItem("cc-accent", "rgb(255, 0, 0)");
    ls.setItem("cc-accent-ink", "#ffffff");
    ls.setItem("cc-density", "compact");
    ls.setItem("cc-density-org", "comfortable");
    ls.setItem("theme", "light");

    expect(bindAppearanceScope(A.email, A.org)).toBe(true);
    expect(ls.getItem(`cc-accent:${SCOPE_A}`)).toBe("rgb(255, 0, 0)");
    expect(ls.getItem(`cc-accent-ink:${SCOPE_A}`)).toBe("#ffffff");
    expect(ls.getItem(`cc-density:${SCOPE_A}`)).toBe("compact");
    expect(ls.getItem(`cc-density-org:${SCOPE_A}`)).toBe("comfortable");
    expect(ls.getItem(`theme:${SCOPE_A}`)).toBe("light");
    // The member keys go. next-themes still needs its bare key.
    expect(ls.getItem("cc-accent")).toBeNull();
    expect(ls.getItem("cc-accent-ink")).toBeNull();
    expect(ls.getItem("cc-density")).toBeNull();
    expect(ls.getItem("theme")).toBe("light");

    // A bare value written later is not adopted by the next account.
    ls.setItem("cc-accent", "rgb(9, 9, 9)");
    bindAppearanceScope(B.email, B.org);
    expect(ls.getItem(`cc-accent:${SCOPE_B}`)).toBeNull();
    expect(ls.getItem(`theme:${SCOPE_B}`)).toBeNull();
  });

  it("an existing scoped value wins over a legacy one", () => {
    ls.setItem(`cc-accent:${SCOPE_A}`, "rgb(1, 2, 3)");
    ls.setItem("cc-accent", "rgb(255, 0, 0)");
    bindAppearanceScope(A.email, A.org);
    expect(ls.getItem(`cc-accent:${SCOPE_A}`)).toBe("rgb(1, 2, 3)");
  });
});

describe("(d) a switch points the next load at the target first", () => {
  it("writes the target's last full scope before it navigates", async () => {
    bindAppearanceScope(A.email, A.org);
    bindAppearanceScope(B.email, B.org);
    bindAppearanceScope(A.email, A.org);
    const pointerAtGo: Array<string | null> = [];
    const go = vi.fn(() => void pointerAtGo.push(ls.getItem(APPEARANCE_SCOPE_KEY)));
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true, email: "B@two.test" })));
    expect(await switchTo(1, go)).toBe(true);
    expect(pointerAtGo).toEqual([SCOPE_B]);
  });

  it("a refused switch leaves the pointer alone", async () => {
    bindAppearanceScope(A.email, A.org);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 409 })));
    expect(await switchTo(1, vi.fn())).toBe(false);
    expect(ls.getItem(APPEARANCE_SCOPE_KEY)).toBe(SCOPE_A);
  });

  it("an email with no remembered org leaves the pointer, and the bind corrects it later", () => {
    bindAppearanceScope(A.email, A.org);
    pointAppearanceAt("new@three.test");
    expect(ls.getItem(APPEARANCE_SCOPE_KEY)).toBe(SCOPE_A);
  });
});

describe("(e) the pointer corrects itself when the session disagrees", () => {
  it("moves to the session's account, then re-reads the store and the mode", () => {
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_A);
    ls.setItem(`theme:${SCOPE_B}`, "light");
    const rehydrate = vi.fn();
    const setMode = vi.fn();
    expect(reconcileAppearanceScope(B.email, B.org, { rehydrate, setMode })).toBe(true);
    expect(ls.getItem(APPEARANCE_SCOPE_KEY)).toBe(SCOPE_B);
    expect(rehydrate).toHaveBeenCalledTimes(1);
    expect(setMode).toHaveBeenCalledWith("light");

    // Agreeing again changes nothing.
    expect(reconcileAppearanceScope(B.email, B.org, { rehydrate, setMode })).toBe(false);
    expect(rehydrate).toHaveBeenCalledTimes(1);
  });

  it("a scope with no mode gets the default mode, not the last account's", () => {
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_A);
    const setMode = vi.fn();
    reconcileAppearanceScope(B.email, B.org, { rehydrate: vi.fn(), setMode });
    expect(setMode).toHaveBeenCalledWith("dark");
  });

  it("a lost org id keeps the last org of that email, and a lost email keeps the pointer", () => {
    bindAppearanceScope(A.email, A.org);
    expect(bindAppearanceScope(A.email, "")).toBe(false);
    expect(bindAppearanceScope(null, null)).toBe(false);
    expect(ls.getItem(APPEARANCE_SCOPE_KEY)).toBe(SCOPE_A);
  });
});

describe("the wiring (source fence)", () => {
  const read = (rel: string) => readFileSync(path.join(__dirname, "..", "..", rel), "utf-8");

  it("ThemeProvider copies the mode BEFORE it binds, and applies org defaults after", () => {
    const src = read("components/ThemeProvider.tsx");
    const mirror = src.indexOf("themeStorage.mirrorMode(");
    const bind = src.indexOf("reconcileAppearanceScope(email");
    // (round 2, P1 rule 2) Once for each identity: `setTheme` is not a dep.
    const deps = src.slice(bind).match(/\}, \[([^\]]*)\]\);/);
    expect(deps?.[1]).toBe("loading, email, orgId, rehydrate");
    // The storage listener is installed before the bind runs.
    expect(src.indexOf("watchAppearanceStorage()")).toBeGreaterThan(0);
    expect(src.indexOf("watchAppearanceStorage()")).toBeLessThan(bind);
    const org = src.indexOf("setOrgDefaults(org)");
    expect(mirror).toBeGreaterThan(0);
    expect(bind).toBeGreaterThan(mirror);
    expect(org).toBeGreaterThan(bind);
  });

  it("a detected sign-out clears the appearance after NextAuth confirms it", () => {
    const hook = read("hooks/useChatSessions.ts");
    expect(hook).toMatch(/^\s*if \(confirmed && !cancelled\) clearSignedOutAppearance\(\);\r?$/m);
  });

  it("the appearance provider mounts inside AccessProvider, so useAccess() resolves", () => {
    const src = read("components/Providers.tsx");
    const open = src.indexOf("<AccessProvider>");
    const close = src.indexOf("</AccessProvider>");
    const at = src.indexOf("<AppearanceProvider />");
    expect(open).toBeGreaterThan(0);
    expect(at).toBeGreaterThan(open);
    expect(at).toBeLessThan(close);
  });
});

describe("review round 2, P1: two tabs on two accounts", () => {
  it("(1) a tab writes to ITS scope, not to the pointer another tab moved", () => {
    bindAppearanceScope(A.email, A.org);
    // Another tab loads as B. Storage changes with no event in this tab.
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_B);
    useAppearanceStore.getState().setAccent("rgb(255, 0, 0)");
    useAppearanceStore.getState().setUserDensity("compact");
    useAppearanceStore.getState().setOrgDefaults({ density: "comfortable", allowUserOverride: true });
    themeStorage.mirrorMode("light");
    expect(tabAppearanceScope()).toBe(SCOPE_A);
    expect(ls.getItem(`cc-accent:${SCOPE_A}`)).toBe("rgb(255, 0, 0)");
    expect(ls.getItem(`cc-density:${SCOPE_A}`)).toBe("compact");
    expect(ls.getItem(`cc-density-org:${SCOPE_A}`)).toBe("comfortable");
    expect(ls.getItem(`theme:${SCOPE_A}`)).toBe("light");
    for (const k of ["cc-accent", "cc-density", "cc-density-org", "theme"]) {
      expect(ls.getItem(`${k}:${SCOPE_B}`)).toBeNull();
    }
  });

  it("(2) a second bind of the same identity changes nothing", () => {
    const apply = { rehydrate: vi.fn(), setMode: vi.fn() };
    expect(reconcileAppearanceScope(A.email, A.org, apply)).toBe(true);
    expect(reconcileAppearanceScope(A.email, A.org, apply)).toBe(false);
    expect(apply.rehydrate).toHaveBeenCalledTimes(1);
    expect(apply.setMode).toHaveBeenCalledTimes(1);
  });

  it("(3) a tab whose pointer another tab moves goes passive: no re-point, no write", () => {
    bindAppearanceScope(A.email, A.org);
    ls.setItem(`theme:${SCOPE_A}`, "light");
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_B);
    onAppearanceStorage({ key: APPEARANCE_SCOPE_KEY, newValue: SCOPE_B });
    expect(isAppearancePassive()).toBe(true);

    const apply = { rehydrate: vi.fn(), setMode: vi.fn() };
    expect(reconcileAppearanceScope(A.email, A.org, apply)).toBe(false);
    expect(ls.getItem(APPEARANCE_SCOPE_KEY)).toBe(SCOPE_B);
    expect(apply.setMode).not.toHaveBeenCalled();
    themeStorage.mirrorMode("dark");
    useAppearanceStore.getState().setAccent("rgb(1, 2, 3)");
    expect(ls.getItem(`theme:${SCOPE_A}`)).toBe("light");
    expect(ls.getItem(`cc-accent:${SCOPE_A}`)).toBeNull();
  });

  it("(3) a tab that loaded with no pointer still binds when a sibling of the SAME account wrote it first", () => {
    // Session restore: two tabs of A load with no pointer. This one reads none.
    expect(isAppearancePassive()).toBe(false);
    themeStorage.getAccent();
    // The faster tab binds A and writes the pointer.
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_A);
    onAppearanceStorage({ key: APPEARANCE_SCOPE_KEY, newValue: SCOPE_A });
    bindAppearanceScope(A.email, A.org);
    expect(isAppearancePassive()).toBe(false);
    useAppearanceStore.getState().setAccent("rgb(1, 2, 3)");
    themeStorage.mirrorMode("light");
    expect(ls.getItem(`cc-accent:${SCOPE_A}`)).toBe("rgb(1, 2, 3)");
    expect(ls.getItem(`theme:${SCOPE_A}`)).toBe("light");
  });

  it("(3) a pointer event that names this tab's own scope keeps it active", () => {
    bindAppearanceScope(A.email, A.org);
    onAppearanceStorage({ key: APPEARANCE_SCOPE_KEY, newValue: SCOPE_A });
    expect(isAppearancePassive()).toBe(false);
  });

  it("(4) a mode that came from another tab is not mirrored, a mode chosen here is", () => {
    bindAppearanceScope(A.email, A.org);
    ls.setItem(`theme:${SCOPE_A}`, "light");
    onAppearanceStorage({ key: "theme", newValue: "dark" });
    themeStorage.mirrorMode("dark");
    expect(ls.getItem(`theme:${SCOPE_A}`)).toBe("light");
    // A removed key reaches next-themes as its default mode.
    onAppearanceStorage({ key: "theme", newValue: null });
    themeStorage.mirrorMode("dark");
    expect(ls.getItem(`theme:${SCOPE_A}`)).toBe("light");
    themeStorage.mirrorMode("dark");
    expect(ls.getItem(`theme:${SCOPE_A}`)).toBe("dark");
  });
});

describe("review round 2, P2: a scope with no org is no scope", () => {
  it("does not bind, adopt or point at `email|`, and the bare keys stay", () => {
    ls.setItem("cc-accent", "rgb(255, 0, 0)");
    ls.setItem("theme", "light");
    const apply = { rehydrate: vi.fn(), setMode: vi.fn() };
    expect(reconcileAppearanceScope(A.email, "", apply)).toBe(false);
    expect(ls.getItem(APPEARANCE_SCOPE_KEY)).toBeNull();
    expect(ls.getItem("cc-accent")).toBe("rgb(255, 0, 0)");
    expect(ls.getItem("cc-accent:a@one.test|")).toBeNull();
    expect(themeStorage.getAccent()).toBe("rgb(255, 0, 0)");

    // The org arrives on the next answer: now the values move, once.
    expect(reconcileAppearanceScope(A.email, A.org, apply)).toBe(true);
    expect(ls.getItem(`cc-accent:${SCOPE_A}`)).toBe("rgb(255, 0, 0)");
    expect(ls.getItem("cc-accent")).toBeNull();
  });

  it("a remembered org still supplies the scope", () => {
    bindAppearanceScope(A.email, A.org);
    expect(appearanceScopeFor(A.email, "")).toBe(SCOPE_A);
    expect(appearanceScopeFor("new@three.test", "")).toBeNull();
  });
});

describe("review round 2, P2: a sign-out leaves no address in a key name", () => {
  const seed = () => {
    bindAppearanceScope(B.email, B.org);
    bindAppearanceScope(A.email, A.org);
    ls.setItem(`cc-accent:${SCOPE_A}`, "red");
    ls.setItem(`theme:${SCOPE_A}`, "light");
    ls.setItem(`cc-density-org:${SCOPE_B}`, "compact");
    ls.setItem("cc-accent:c@three.test|org-3", "blue");
    ls.setItem("cc-appearance-last:c@three.test", "c@three.test|org-3");
  };
  const leftovers = () =>
    Array.from({ length: ls.length }, (_, i) => ls.key(i) ?? "").filter((k) => /a@one\.test|b@two\.test/.test(k));

  it("clears every key of each account, the pointer, and nobody else's", () => {
    seed();
    clearAppearanceForAccounts(["A@one.test", "b@two.test"]);
    expect(leftovers()).toEqual([]);
    expect(ls.getItem(APPEARANCE_SCOPE_KEY)).toBeNull();
    expect(ls.getItem("cc-accent:c@three.test|org-3")).toBe("blue");
    expect(ls.getItem("cc-appearance-last:c@three.test")).toBe("c@three.test|org-3");
    // This tab is signed out: it falls back to the bare keys.
    expect(tabAppearanceScope()).toBeNull();
  });

  it("Sign out of all accounts clears them", async () => {
    seed();
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true, emails: ["a@one.test", "b@two.test"] })));
    await signOutAll();
    expect(leftovers()).toEqual([]);
  });

  it("a detected sign-out clears the account this tab showed, and only it", () => {
    seed();
    clearSignedOutAppearance();
    expect(leftovers().filter((k) => k.includes("a@one.test"))).toEqual([]);
    expect(ls.getItem(`cc-density-org:${SCOPE_B}`)).toBe("compact");
  });
});

describe("the boot script survives a full storage quota", () => {
  it("a throwing mode copy does not skip the density and the accent", () => {
    ls.setItem(APPEARANCE_SCOPE_KEY, SCOPE_A);
    ls.setItem(`theme:${SCOPE_A}`, "light");
    ls.setItem(`cc-density:${SCOPE_A}`, "compact");
    ls.setItem(`cc-accent:${SCOPE_A}`, "rgb(255, 0, 0)");
    const full = {
      getItem: (k: string) => ls.getItem(k),
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: (k: string) => ls.removeItem(k),
    };
    const props: Record<string, string> = {};
    const documentElement = { style: { setProperty: (p: string, v: string) => void (props[p] = v) } };
    new Function("window", "document", themeBootScript())({ localStorage: full }, { documentElement });
    expect(props["--ui-scale"]).toBe("0.92");
    expect(props["--primary"]).toBe("rgb(255, 0, 0)");
  });
});
