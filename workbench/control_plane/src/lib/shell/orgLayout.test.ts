import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, peek, read } from "@/lib/dataCache";
import { NAV_SECTIONS, visibleSections } from "@/lib/nav";
import { applyData, applyError, initialState } from "@/lib/useCachedResource";
import {
  EMPTY_ORG_LAYOUT,
  ORG_LAYOUT_WAIT_MS,
  fetchOrgLayout,
  keepOrgLayoutCopy,
  orgDefaultFor,
  orgLayoutFetcher,
  orgLayoutKey,
  orgLayoutRead,
  orgLayoutSettled,
  parseOrgLayout,
  readCachedOrgLayout,
  type OrgLayout,
} from "./orgLayout";
import { clearCachedLayouts, orgLayoutCacheKey, shellCacheKey } from "./shellCache";
import { SHELL_WAIT_MS } from "./shellPrefs";
import { EMPTY_SHELL, STARRABLE, shellLayout, starBase, starrableHeld, starredHrefs, toggleStar, type StoredShell } from "./presets";
import { shellSidebar } from "./shellNav";

/**
 * PKG-17b (`package_host.md` §9.1). The org default is a fallback. The
 * precedence table mirrors `tests/unit/test_shell_layout.py::test_precedence`.
 */

const everything = visibleSections(NAV_SECTIONS.flatMap((s) => s.items.map((p) => p.feature ?? "")), true);
const [A, B, C] = starrableHeld(everything).map((p) => p.href);
const stored = (over: Partial<StoredShell> = {}): StoredShell => ({ ...EMPTY_SHELL, ...over });
const doc = {
  roles: {
    member: { preset: "sales-manager", pins: [A, B] },
    admin: { pins: [A] },
  },
};
const layout = parseOrgLayout(doc);
const resolve = (s: StoredShell | undefined, roles: string[]) => {
  const l = shellLayout(s, roles, orgDefaultFor(layout, roles));
  return { pins: l.pins, preset: l.preset.id };
};
const NONE = shellLayout(undefined, []).preset.id;

describe("parseOrgLayout", () => {
  it("reads a good body", () => {
    expect(layout.roles.member).toEqual({ preset: "sales-manager", pins: [A, B] });
    expect(layout.roles.admin).toEqual({ preset: null, pins: [A] });
  });

  it.each([null, undefined, 7, "x", [], {}, { roles: null }, { roles: [] }, { roles: "bad" }])(
    "reads %j as no default and never throws",
    (body) => {
      expect(parseOrgLayout(body)).toEqual(EMPTY_ORG_LAYOUT);
    },
  );

  it("drops an unknown pane, a repeat and a non-string, and keeps the rest", () => {
    const got = parseOrgLayout({ roles: { member: { pins: [A, "/nope", 4, null, A, "https://x.test", B] } } });
    expect(got.roles.member?.pins).toEqual([A, B]);
  });

  it("keeps an empty list of pins (it is a choice)", () => {
    expect(parseOrgLayout({ roles: { member: { pins: [] } } }).roles.member).toEqual({ preset: null, pins: [] });
  });

  it("drops an unknown preset, an unknown role, and an entry with nothing usable", () => {
    const got = parseOrgLayout({
      roles: { member: { preset: "ceo", pins: "x" }, root: { pins: [A] }, guest: { preset: "engineer" }, owner: 5 },
    });
    expect(got.roles).toEqual({ guest: { preset: "engineer", pins: null } });
  });

  it("does not read a role from the prototype", () => {
    expect(parseOrgLayout({ roles: Object.create({ member: { pins: [A] } }) }).roles).toEqual({});
  });
});

describe("orgDefaultFor", () => {
  const all = parseOrgLayout({
    roles: {
      owner: { pins: [C] },
      admin: { pins: [B] },
      manager: { pins: [A, B] },
      member: { pins: [A] },
      guest: { pins: [] },
    },
  });
  it("the highest role with an entry wins, in the order owner to guest", () => {
    expect(orgDefaultFor(all, ["guest", "member", "owner"])?.pins).toEqual([C]);
    expect(orgDefaultFor(all, ["member", "admin"])?.pins).toEqual([B]);
    expect(orgDefaultFor(all, ["guest", "manager"])?.pins).toEqual([A, B]);
    expect(orgDefaultFor(all, ["guest"])?.pins).toEqual([]);
  });
  it("skips a role with no entry and reads the next", () => {
    expect(orgDefaultFor(layout, ["admin", "member"])?.pins).toEqual([A]);
    expect(orgDefaultFor(parseOrgLayout({ roles: { member: { pins: [B] } } }), ["owner", "member"])?.pins).toEqual([B]);
  });
  it("is case-blind, and null for no role, a custom role, or no layout", () => {
    expect(orgDefaultFor(all, ["OWNER"])?.pins).toEqual([C]);
    expect(orgDefaultFor(all, [])).toBeNull();
    expect(orgDefaultFor(all, ["finance-lead"])).toBeNull();
    expect(orgDefaultFor(EMPTY_ORG_LAYOUT, ["owner"])).toBeNull();
    expect(orgDefaultFor(null, ["owner"])).toBeNull();
  });
  it("returns a copy", () => {
    const d = orgDefaultFor(all, ["owner"])!;
    d.pins!.push(A);
    expect(all.roles.owner?.pins).toEqual([C]);
  });
});

describe("precedence (mirrors resolve_layout)", () => {
  it("member pins win, with their preset", () => {
    expect(resolve(stored({ pins: [C], known: [A, B, C], preset: "engineer" }), ["member"])).toEqual({
      pins: [C],
      preset: "engineer",
    });
  });
  it("an empty list of member pins wins and the preset falls to the role default", () => {
    expect(resolve(stored({ pins: [], known: [A] }), ["member"])).toEqual({ pins: [], preset: "sales-manager" });
  });
  it("null pins take the role default pins and preset", () => {
    expect(resolve(stored(), ["member"])).toEqual({ pins: [A, B], preset: "sales-manager" });
  });
  it("many roles: the highest entry wins, and with no preset the client role default stands", () => {
    expect(resolve(stored(), ["member", "admin"])).toEqual({ pins: [A], preset: shellLayout(stored(), ["admin"]).preset.id });
  });
  it("a role with no entry gets nothing", () => {
    expect(resolve(stored(), ["guest"])).toEqual({ pins: null, preset: shellLayout(stored(), ["guest"]).preset.id });
  });
  it("null pins and an own preset: pins from the default, preset the member's", () => {
    expect(resolve(stored({ preset: "engineer" }), ["member"])).toEqual({ pins: [A, B], preset: "engineer" });
  });
  it("no stored layout yet (loading or a failed read): the default does not apply", () => {
    expect(resolve(undefined, ["member"])).toEqual({ pins: null, preset: NONE });
  });
  it("a default whose pins are null leaves the member's pins alone", () => {
    const l = shellLayout(stored(), ["guest"], { preset: "engineer", pins: null });
    expect(l.pins).toBeNull();
    expect(l.preset.id).toBe("engineer");
  });
});

describe("the sidebar", () => {
  const features = NAV_SECTIONS.flatMap((s) => s.items.map((p) => p.feature ?? ""));
  const held = visibleSections(features, true);
  const hrefs = (secs: ReturnType<typeof shellSidebar>) => secs.flatMap((s) => s.items.map((p) => p.href));

  it("shows exactly the default stars for a member with null pins", () => {
    const stars = starredHrefs(shellLayout(stored(), ["member"], orgDefaultFor(layout, ["member"])), held);
    expect(stars).toEqual(new Set([A, B]));
  });
  it("a default pane that the member does not hold stays hidden", () => {
    const some = visibleSections(features.filter((f) => f !== ""), false);
    const heldHrefs = new Set(some.flatMap((s) => s.items.map((p) => p.href)));
    const unheld = [...STARRABLE].find((h) => !heldHrefs.has(h));
    if (!unheld) return;
    const l = shellLayout(stored(), ["member"], { preset: null, pins: [unheld] });
    expect(starredHrefs(l, some)).toEqual(new Set());
  });
  it("with no org default the sidebar and the stars are those of today", () => {
    for (const s of [undefined, stored(), stored({ pins: [A], known: [A, B] }), stored({ pins: [], known: [...STARRABLE] })]) {
      const today = shellLayout(s, ["member"]);
      const withNone = shellLayout(s, ["member"], orgDefaultFor(EMPTY_ORG_LAYOUT, ["member"]));
      expect(withNone).toEqual(today);
      expect(shellSidebar(held, withNone, false)).toEqual(shellSidebar(held, today, false));
      expect(starredHrefs(withNone, held)).toEqual(starredHrefs(today, held));
    }
  });
  it("a member with stored pins keeps them, whatever the default says", () => {
    const own = stored({ pins: [C], known: [...STARRABLE] });
    const l = shellLayout(own, ["member"], orgDefaultFor(layout, ["member"]));
    expect(hrefs(shellSidebar(held, l, false))).toEqual(hrefs(shellSidebar(held, shellLayout(own, ["member"]), false)));
  });
  it("a star toggle builds on the default stars and saves the member's own", () => {
    const l = shellLayout(stored(), ["member"], orgDefaultFor(layout, ["member"]));
    const base = starBase(stored(), l);
    expect(base.pins).toEqual([A, B]);
    const next = toggleStar(base, held, A);
    expect(next.pins).not.toContain(A);
    expect(next.pins).toContain(B);
    // A member with their own pins is not touched.
    const own = stored({ pins: [C], known: [C] });
    expect(starBase(own, l)).toBe(own);
  });
});

describe("fetchOrgLayout", () => {
  const reply = (r: () => Response | Promise<Response>) => vi.stubGlobal("fetch", vi.fn(async () => r()));
  afterEach(() => vi.unstubAllGlobals());

  it("a 200 reads the layout", async () => {
    reply(() => new Response(JSON.stringify(doc), { status: 200 }));
    expect((await fetchOrgLayout()).roles.member?.pins).toEqual([A, B]);
  });
  it("a 404 (the host is off) is no org default", async () => {
    reply(() => new Response(JSON.stringify(doc), { status: 404 }));
    expect(await fetchOrgLayout()).toEqual(EMPTY_ORG_LAYOUT);
  });
  it("a 200 with a shape it does not know is no org default", async () => {
    reply(() => new Response(JSON.stringify({ roles: "bad" }), { status: 200 }));
    expect(await fetchOrgLayout()).toEqual(EMPTY_ORG_LAYOUT);
  });
  it.each([400, 401, 403, 500, 502, 503])("a %i throws, so the cache keeps the last good answer", async (status) => {
    reply(() => new Response(JSON.stringify(doc), { status }));
    await expect(fetchOrgLayout()).rejects.toThrow();
  });
  it("a network fault and a body that is not JSON throw", async () => {
    reply(() => {
      throw new TypeError("fetch failed");
    });
    await expect(fetchOrgLayout()).rejects.toThrow();
    reply(() => new Response("not json", { status: 200 }));
    await expect(fetchOrgLayout()).rejects.toThrow();
  });
});

describe("a failed refresh keeps the previous default (P1-b)", () => {
  const reply = (r: () => Response | Promise<Response>) => vi.stubGlobal("fetch", vi.fn(async () => r()));
  beforeEach(() => clearAll());
  afterEach(() => {
    vi.unstubAllGlobals();
    clearAll();
  });

  it("the cache and the hook state keep the default after a 502", async () => {
    const key = orgLayoutKey();
    reply(() => new Response(JSON.stringify(doc), { status: 200 }));
    const first = applyData(await read(key, fetchOrgLayout, { force: true }));
    reply(() => new Response("{}", { status: 502 }));
    const next = await read(key, fetchOrgLayout, { force: true }).then(
      (d) => applyData(d),
      (e: unknown) => applyError(first, String(e)),
    );
    expect(peek<OrgLayout>(key)?.data).toEqual(layout);
    expect(next.data).toEqual(layout);
    const drawn = orgLayoutRead({ data: next.data, error: next.error, copy: undefined });
    expect(drawn.layout).toEqual(layout);
    expect(drawn.state).toBe("answered");
  });

  it("with no earlier answer and no copy, a failure is no org default and settles", async () => {
    reply(() => {
      throw new TypeError("fetch failed");
    });
    const start = initialState<OrgLayout>(undefined);
    const next = await fetchOrgLayout().then(applyData, (e: unknown) => applyError(start, String(e)));
    const drawn = orgLayoutRead({ data: next.data, error: next.error, copy: undefined });
    expect(drawn).toEqual({ layout: EMPTY_ORG_LAYOUT, state: "answered" });
    expect(orgLayoutSettled({ storedPins: null, orgState: drawn.state, elapsedMs: 0 })).toBe(true);
    expect(shellLayout(stored(), ["member"], orgDefaultFor(drawn.layout, ["member"]))).toEqual(
      shellLayout(stored(), ["member"]),
    );
  });

  it("a failure with a browser copy keeps the copy", () => {
    expect(orgLayoutRead({ data: undefined, error: "org layout 502", copy: layout }).layout).toEqual(layout);
  });
});

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
}

describe("the browser copy of the org default (P1-a)", () => {
  const SCOPE = "priya@acme.example|org-acme";
  const OTHER = "ben@beta.example|org-beta";
  let storage: MemoryStorage;
  beforeEach(() => {
    storage = new MemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("is keyed per account, apart from the member's layout", () => {
    expect(orgLayoutCacheKey(SCOPE)).toBe(`cc-shell-org-layout:${SCOPE}`);
    expect(orgLayoutCacheKey(SCOPE)).not.toBe(shellCacheKey(SCOPE));
    expect(orgLayoutCacheKey(null)).toBeNull();
    expect(orgLayoutCacheKey("no-scope")).toBeNull();
  });

  it("an answer with a default is kept, and only for that account", () => {
    keepOrgLayoutCopy(SCOPE, layout);
    expect(readCachedOrgLayout(SCOPE)).toEqual(layout);
    expect(readCachedOrgLayout(OTHER)).toBeUndefined();
  });

  it("a 404 or an empty answer writes the 'no default' marker, and never removes the key", () => {
    keepOrgLayoutCopy(SCOPE, layout);
    keepOrgLayoutCopy(SCOPE, EMPTY_ORG_LAYOUT);
    expect(readCachedOrgLayout(SCOPE)).toEqual(EMPTY_ORG_LAYOUT);
    expect(JSON.parse(storage.getItem(orgLayoutCacheKey(SCOPE)!)!)).toEqual({ v: 1, layout: { roles: {} } });
  });

  it.each([
    // what is stored, what reads back
    ["the marker", { v: 1, layout: { roles: {} } }, EMPTY_ORG_LAYOUT],
    ["a default", { v: 1, layout: doc }, layout],
    ["a default with a junk entry", { v: 1, layout: { roles: { member: { pins: [A, "/nope"] }, admin: 7 } } }, parseOrgLayout({ roles: { member: { pins: [A] } } })],
  ] as const)("the copy reads back: %s", (_name, value, back) => {
    storage.setItem(orgLayoutCacheKey(SCOPE)!, JSON.stringify(value));
    expect(readCachedOrgLayout(SCOPE)).toEqual(back);
    expect(orgLayoutRead({ data: undefined, error: null, copy: readCachedOrgLayout(SCOPE) }).state).toBe("copy");
  });

  it("a bad copy reads as no copy and never throws", () => {
    const hostile = [
      "{not json",
      "7",
      "null",
      '"x"',
      "[]",
      JSON.stringify({ roles: "bad" }),
      JSON.stringify(doc), // the bare shape, with no version
      JSON.stringify({ roles: {} }), // the bare empty shape is not the marker
      JSON.stringify({ v: 2, layout: doc }),
      JSON.stringify({ v: "1", layout: doc }),
      JSON.stringify({ v: 1 }),
      JSON.stringify({ v: 1, layout: null }),
      JSON.stringify({ v: 1, layout: "bad" }),
      JSON.stringify({ v: 1, layout: { roles: "bad" } }),
      JSON.stringify({ v: 1, layout: { roles: [] } }),
      JSON.stringify({ v: 1, layout: [] }),
    ];
    for (const bad of hostile) {
      storage.setItem(orgLayoutCacheKey(SCOPE)!, bad);
      expect(readCachedOrgLayout(SCOPE)).toBeUndefined();
      expect(orgLayoutRead({ data: undefined, error: null, copy: readCachedOrgLayout(SCOPE) }).state).toBe("pending");
    }
    for (const bad of ["{not json", "7", "null", JSON.stringify({ roles: "bad" })]) {
      storage.setItem(orgLayoutCacheKey(SCOPE)!, bad);
      expect(readCachedOrgLayout(SCOPE)).toBeUndefined();
    }
    vi.stubGlobal("window", undefined);
    expect(() => keepOrgLayoutCopy(SCOPE, layout)).not.toThrow();
    expect(readCachedOrgLayout(SCOPE)).toBeUndefined();
  });

  it("a sign-out clears the marker too, in every org of that email", () => {
    keepOrgLayoutCopy(SCOPE, EMPTY_ORG_LAYOUT);
    keepOrgLayoutCopy("priya@acme.example|org-beta", EMPTY_ORG_LAYOUT);
    keepOrgLayoutCopy(OTHER, EMPTY_ORG_LAYOUT);
    clearCachedLayouts(["Priya@Acme.example"]);
    expect(storage.getItem(orgLayoutCacheKey(SCOPE)!)).toBeNull();
    expect(readCachedOrgLayout(SCOPE)).toBeUndefined();
    expect(readCachedOrgLayout("priya@acme.example|org-beta")).toBeUndefined();
    expect(readCachedOrgLayout(OTHER)).toEqual(EMPTY_ORG_LAYOUT);
  });

  it("a sign-out clears it with the member's layout, in every org of that email", () => {
    keepOrgLayoutCopy(SCOPE, layout);
    keepOrgLayoutCopy("priya@acme.example|org-beta", layout);
    keepOrgLayoutCopy(OTHER, layout);
    clearCachedLayouts(["Priya@Acme.example"]);
    expect(readCachedOrgLayout(SCOPE)).toBeUndefined();
    expect(readCachedOrgLayout("priya@acme.example|org-beta")).toBeUndefined();
    expect(readCachedOrgLayout(OTHER)).toEqual(layout);
  });

  it("the copy draws the first frame, before any answer, and the shell need not wait", () => {
    keepOrgLayoutCopy(SCOPE, layout);
    const first = orgLayoutRead({ data: undefined, error: null, copy: readCachedOrgLayout(SCOPE) });
    expect(first).toEqual({ layout, state: "copy" });
    expect(orgLayoutSettled({ storedPins: null, orgState: first.state, elapsedMs: 0 })).toBe(true);
    const stars = starredHrefs(shellLayout(stored(), ["member"], orgDefaultFor(first.layout, ["member"])), everything);
    expect(stars).toEqual(new Set([A, B]));
  });

  it("the answer wins over the copy", () => {
    const newer = parseOrgLayout({ roles: { member: { pins: [C] } } });
    expect(orgLayoutRead({ data: newer, error: null, copy: layout }).layout).toEqual(newer);
  });

  it("the hook reads the copy for its first render, and only its scoped fetch keeps an answer (source fence)", () => {
    const src = readFileSync(fileURLToPath(new URL("./orgLayout.ts", import.meta.url)), "utf-8");
    const hook = src.slice(src.indexOf("export function useOrgLayout"));
    expect(hook).toMatch(/useMemo\(\(\) => \(enabled \? readCachedOrgLayout\(scope\) : undefined\)/);
    expect(hook).toMatch(/const fetcher = useMemo\(\(\) => orgLayoutFetcher\(scope\), \[scope\]\)/);
    expect(hook).toMatch(/useCachedResource<OrgLayout>\(enabled \? orgLayoutKey\(\) : null, fetcher,/);
    expect(hook).toMatch(/orgLayoutRead\(\{ data: res\.data, error: res\.error, copy \}\)/);
    // P2: the hook never writes the copy from `res.data`, which can hold the
    // answer of the previous account after a switch in the same tab.
    const code = hook.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/keepOrgLayoutCopy|writeCachedOrgLayout|useEffect/);
    expect(src).not.toMatch(/clearCachedOrgLayout|removeItem/);
  });
});

describe("the 'no default' marker and the scoped write (review P1, P2)", () => {
  const SCOPE = "priya@acme.example|org-acme";
  const NEXT = "priya@acme.example|org-beta";
  let storage: MemoryStorage;
  const reply = (r: () => Response | Promise<Response>) => vi.stubGlobal("fetch", vi.fn(async () => r()));
  const ok = (body: unknown) => () => new Response(JSON.stringify(body), { status: 200 });
  const status = (n: number) => () => new Response(JSON.stringify(doc), { status: n });
  beforeEach(() => {
    storage = new MemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
  });
  afterEach(() => vi.unstubAllGlobals());

  /** A full page load: the first render reads the copy, with no answer yet. */
  const firstFrame = (scope: string) => orgLayoutRead({ data: undefined, error: null, copy: readCachedOrgLayout(scope) });

  it.each([
    ["a 404 (the host is off)", status(404)],
    ["an empty 200", ok({ roles: {} })],
    ["a 200 it cannot read", ok({ roles: "bad" })],
  ])("org with no default, %s: the second full load has state copy, an empty layout and NO hold", async (_n, r) => {
    // First load: no copy, so the shell holds.
    expect(firstFrame(SCOPE).state).toBe("pending");
    expect(orgLayoutSettled({ storedPins: null, orgState: "pending", elapsedMs: 0 })).toBe(false);
    reply(r);
    expect(await orgLayoutFetcher(SCOPE)()).toEqual(EMPTY_ORG_LAYOUT);
    // Second load.
    const second = firstFrame(SCOPE);
    expect(second).toEqual({ layout: EMPTY_ORG_LAYOUT, state: "copy" });
    expect(orgLayoutSettled({ storedPins: null, orgState: second.state, elapsedMs: 0 })).toBe(true);
    expect(orgLayoutSettled({ storedPins: undefined, orgState: second.state, elapsedMs: 0 })).toBe(true);
    expect(shellLayout(stored(), ["member"], orgDefaultFor(second.layout, ["member"]))).toEqual(
      shellLayout(stored(), ["member"]),
    );
  });

  it("a default saved after a marker replaces it", async () => {
    reply(status(404));
    await orgLayoutFetcher(SCOPE)();
    expect(firstFrame(SCOPE).layout).toEqual(EMPTY_ORG_LAYOUT);
    reply(ok(doc));
    await orgLayoutFetcher(SCOPE)();
    expect(firstFrame(SCOPE)).toEqual({ layout, state: "copy" });
  });

  it("a 404 after a default replaces it with the marker", async () => {
    reply(ok(doc));
    await orgLayoutFetcher(SCOPE)();
    reply(status(404));
    await orgLayoutFetcher(SCOPE)();
    expect(firstFrame(SCOPE)).toEqual({ layout: EMPTY_ORG_LAYOUT, state: "copy" });
  });

  it.each([401, 403, 500, 502, 503])("a %i keeps the marker and the copy untouched", async (n) => {
    reply(status(404));
    await orgLayoutFetcher(SCOPE)();
    const marker = storage.getItem(orgLayoutCacheKey(SCOPE)!);
    reply(status(n));
    await expect(orgLayoutFetcher(SCOPE)()).rejects.toThrow();
    expect(storage.getItem(orgLayoutCacheKey(SCOPE)!)).toBe(marker);
    expect(firstFrame(SCOPE)).toEqual({ layout: EMPTY_ORG_LAYOUT, state: "copy" });

    reply(ok(doc));
    await orgLayoutFetcher(SCOPE)();
    reply(status(n));
    await expect(orgLayoutFetcher(SCOPE)()).rejects.toThrow();
    expect(firstFrame(SCOPE).layout).toEqual(layout);
  });

  it("a network fault keeps the copy untouched", async () => {
    reply(ok(doc));
    await orgLayoutFetcher(SCOPE)();
    reply(() => {
      throw new TypeError("fetch failed");
    });
    await expect(orgLayoutFetcher(SCOPE)()).rejects.toThrow();
    expect(firstFrame(SCOPE).layout).toEqual(layout);
  });

  it("no scope: the fetch answers and writes nothing", async () => {
    reply(ok(doc));
    expect(await orgLayoutFetcher(null)()).toEqual(layout);
    expect(storage.length).toBe(0);
  });

  it("scope A answered, then the scope is B with A's answer still in hand: B's copy never gets A's layout", async () => {
    reply(ok(doc));
    const data = await orgLayoutFetcher(SCOPE)(); // res.data of scope A
    expect(readCachedOrgLayout(SCOPE)).toEqual(layout);
    // The account switches in the same tab. `res.data` still holds A's
    // answer, and the hook writes nothing from it.
    expect(data).toEqual(layout);
    expect(readCachedOrgLayout(NEXT)).toBeUndefined();
    // B's first read fails: B has no copy, and A's default never leaks in.
    reply(status(502));
    await expect(orgLayoutFetcher(NEXT)()).rejects.toThrow();
    expect(readCachedOrgLayout(NEXT)).toBeUndefined();
    expect(firstFrame(NEXT).state).toBe("pending");
    // B's own answer is kept under B, and A keeps its own.
    reply(status(404));
    await orgLayoutFetcher(NEXT)();
    expect(readCachedOrgLayout(NEXT)).toEqual(EMPTY_ORG_LAYOUT);
    expect(readCachedOrgLayout(SCOPE)).toEqual(layout);
  });

  it("a read of A that lands after the switch to B is kept under A only", async () => {
    let land: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((res) => (land = res))));
    const inFlight = orgLayoutFetcher(SCOPE)();
    orgLayoutFetcher(NEXT); // the hook makes B's fetch on the switch
    land(new Response(JSON.stringify(doc), { status: 200 }));
    await inFlight;
    expect(readCachedOrgLayout(SCOPE)).toEqual(layout);
    expect(readCachedOrgLayout(NEXT)).toBeUndefined();
  });
});

describe("the hold while the org default is out (P1-a, cold case)", () => {
  const W = ORG_LAYOUT_WAIT_MS;
  it("the wait is short, and shorter than the sidebar's own wait", () => {
    expect(W).toBe(1_500);
    expect(W).toBeLessThan(SHELL_WAIT_MS);
  });
  it.each([
    // storedPins, orgState, elapsedMs, settled
    [null, "pending", 0, false],
    [null, "pending", W - 1, false],
    [undefined, "pending", 0, false],
    [null, "pending", W, true],
    [null, "pending", W * 10, true],
    [undefined, "pending", W, true],
    [null, "copy", 0, true],
    [null, "answered", 0, true],
    [undefined, "answered", 0, true],
    [[], "pending", 0, true],
    [["/tasks"], "pending", 0, true],
    [["/tasks"], "answered", 0, true],
  ] as const)("pins %j, org %s, %i ms: settled %s", (storedPins, orgState, elapsedMs, settled) => {
    expect(orgLayoutSettled({ storedPins, orgState, elapsedMs })).toBe(settled);
  });
  it("useShellPrefs holds only through this rule, and only with a stored layout (source fence)", () => {
    const src = readFileSync(fileURLToPath(new URL("./shellPrefs.ts", import.meta.url)), "utf-8");
    expect(src).toMatch(/setTimeout\(\(\) => setOrgWaited\(true\), ORG_LAYOUT_WAIT_MS\)/);
    expect(src).toMatch(/storedPins: stored\.pins,\s*orgState: org\.state,\s*elapsedMs: orgWaited \? ORG_LAYOUT_WAIT_MS : 0/);
    expect(src).toMatch(/loading: enabled && \(\(stored === undefined && res\.loading\) \|\| orgHold\)/);
    expect(src).toMatch(/const org = useOrgLayout\(enabled, scope\)/);
  });
});

describe("case C: a member with their own three stars", () => {
  const all = [...STARRABLE];
  const own = stored({ pins: ["/calendar", "/people", "/approvals"], known: all });
  it("the member's three win, not the org default and not every app", () => {
    const l = shellLayout(own, ["member"], orgDefaultFor(layout, ["member"]));
    const held = new Set(everything.flatMap((s) => s.items.map((p) => p.href)));
    const expected = new Set(["/calendar", "/people", "/approvals"].filter((h) => held.has(h)));
    expect(expected.size).toBeGreaterThan(0);
    expect(starredHrefs(l, everything)).toEqual(expected);
    expect(starredHrefs(l, everything)).not.toEqual(new Set([A, B]));
  });
  it("an app outside a short `known` reads as new and shows (behaviour of `starredHrefs`, kept)", () => {
    const short = stored({ pins: ["/calendar"], known: ["/calendar", "/people"] });
    const stars = starredHrefs(shellLayout(short, ["member"], orgDefaultFor(layout, ["member"])), everything)!;
    const outside = starrableHeld(everything).map((p) => p.href).filter((h) => h !== "/calendar" && h !== "/people");
    expect(outside.length).toBeGreaterThan(0);
    for (const h of outside) expect(stars.has(h)).toBe(true);
    expect(stars.has("/people")).toBe(false);
  });
});

describe("a star toggle builds on the stars on screen (P2 fence)", () => {
  const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
  const BASE = /const base = starBase\(\{ \.\.\.EMPTY_SHELL, \.\.\.\(latestShell\(\) \?\? shell\.stored\) \}, shell\.layout\);/;
  it.each(["../../components/Sidebar.tsx", "./AppLauncher.tsx"])("%s builds the toggle on starBase", (rel) => {
    const src = read(rel);
    expect(src).toMatch(BASE);
    // The one toggle reads that base, and no other call builds on a raw value.
    expect(src.match(/toggleStar\(/g)?.length).toBe(1);
    expect(src).toMatch(/toggleStar\(base, /);
  });
  it("the toggle path: null pins with an org default unstar one default app and keep the other", () => {
    const l = shellLayout(stored(), ["member"], orgDefaultFor(layout, ["member"]));
    const base = starBase({ ...EMPTY_SHELL, ...stored() }, l);
    const next = { ...base, ...toggleStar(base, everything, B) };
    expect(starredHrefs(next, everything)).toEqual(new Set([A]));
    // Without starBase, the same click starts from every app.
    const raw = { ...EMPTY_SHELL, ...stored() };
    const wrong = { ...raw, ...toggleStar(raw, everything, B) };
    expect(starredHrefs(wrong, everything)?.size ?? 0).toBeGreaterThan(1);
  });
});
