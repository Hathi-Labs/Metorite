import { afterEach, describe, expect, it, vi } from "vitest";

import { NAV_SECTIONS, visibleSections } from "@/lib/nav";
import { EMPTY_ORG_LAYOUT, fetchOrgLayout, orgDefaultFor, parseOrgLayout } from "./orgLayout";
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
  it.each([404, 500, 502])("a %i is no org default", async (status) => {
    reply(() => new Response(JSON.stringify(doc), { status }));
    expect(await fetchOrgLayout()).toEqual(EMPTY_ORG_LAYOUT);
  });
  it("a malformed body and a network fault are no org default and never throw", async () => {
    reply(() => new Response("not json", { status: 200 }));
    expect(await fetchOrgLayout()).toEqual(EMPTY_ORG_LAYOUT);
    reply(() => {
      throw new TypeError("fetch failed");
    });
    expect(await fetchOrgLayout()).toEqual(EMPTY_ORG_LAYOUT);
  });
});
