/**
 * The shell nav (NS-2): the sidebar, the account menu and All apps, read
 * from the manifest. Each case names the member it is about, because what a
 * member sees is the thing under test.
 */
import { describe, expect, it } from "vitest";

import { PANES, visibleSections, type NavPane, type NavSection } from "@/lib/nav";
import {
  CHANGE_LAYOUT_ITEM,
  MY_ACCESS,
  TEAM_GROUPS,
  HOME_PANE,
  accountLinks,
  accountMenu,
  desktopFrame,
  homePane,
  isActive,
  launcherGroups,
  shellSidebar,
  strayCount,
} from "./shellNav";

const ALL = PANES.map((p) => p.feature).filter((f): f is string => !!f);
const shape = (sections: NavSection[]) => sections.map((s) => [s.id, s.items.map((p) => p.href)]);

describe("the sidebar holds places to work, by team", () => {
  it("for a fully granted admin: four groups, Organisation in Admin, and no page about the member", () => {
    expect(shape(shellSidebar(visibleSections(ALL, true)))).toEqual([
      ["personal", ["/tasks", "/calendar", "/email", "/whatsapp", "/artifacts"]],
      ["across", ["/projects", "/crm", "/people"]],
      ["studio", ["/chat"]],
      ["admin", ["/approvals", "/settings/organization", "/integrations"]],
    ]);
  });

  // Owner, 2026-10-09: "organization should be an app under admin if I have
  // access to organization". A member who is not an admin never sees it.
  it("for a member who is not an admin: no Organisation anywhere in the sidebar", () => {
    const hrefs = shellSidebar(visibleSections(ALL, false)).flatMap((s) => s.items.map((p) => p.href));
    expect(hrefs).not.toContain("/settings/organization");
    expect(hrefs).toContain("/approvals");
  });

  it("keeps Chat and Approvals in the sidebar until NS-6 builds the dock and the bell", () => {
    const hrefs = shellSidebar(visibleSections(ALL, true)).flatMap((s) => s.items.map((p) => p.href));
    expect(hrefs).toContain("/chat");
    expect(hrefs).toContain("/approvals");
  });

  it("for a member with tasks and email only: no empty heading", () => {
    // Integrations is open to every member since D-EA-9 (2026-10-14), for
    // the AI agents view, so Admin holds it and nothing else.
    expect(shape(shellSidebar(visibleSections(["tasks", "email"], false)))).toEqual([
      ["personal", ["/tasks", "/calendar", "/email"]],
      ["admin", ["/integrations"]],
    ]);
  });

  it("for a member who holds nothing: Integrations only (D-EA-9)", () => {
    expect(shape(shellSidebar(visibleSections([], false)))).toEqual([["admin", ["/integrations"]]]);
  });

  it("for an unresolved viewer: nothing, never everything (§8.1)", () => {
    expect(shellSidebar(visibleSections(null, true))).toEqual([]);
  });

  it("orders the groups as TEAM_GROUPS names them", () => {
    expect(TEAM_GROUPS.map((g) => g.label)).toEqual([
      "Personal Center",
      "Across teams",
      "AI Studio",
      "Admin",
    ]);
  });

  it("puts a pane with a team the list does not name under Across teams", () => {
    const odd: NavPane = { href: "/invoices", label: "Invoices", icon: "Receipt", note: "", launch: "live", team: "finance" };
    expect(shape(shellSidebar([{ id: "x", label: "x", items: [odd] }]))).toEqual([["across", ["/invoices"]]]);
  });
});

describe("the account menu holds pages about the member", () => {
  it("for an admin: My Profile, My access and Appearance, and NOT Organisation", () => {
    const links = accountLinks(visibleSections(ALL, true));
    expect(links.map((l) => l.label)).toEqual(["My Profile", "My access", "Appearance"]);
    // Organisation is an app in Admin now (owner, 2026-10-09), so it has one
    // door, the sidebar. A second door here would be two places for one page.
    expect(links.map((l) => l.href)).not.toContain("/settings/organization");
  });

  it("for a member who is not an admin: the same three rows", () => {
    expect(accountLinks(visibleSections(ALL, false)).map((l) => l.href)).toEqual([
      "/people/me",
      MY_ACCESS.href,
      "/settings/appearance",
    ]);
  });

  it("for an unresolved viewer: no row at all, not My access alone (§8.1)", () => {
    expect(accountLinks(visibleSections(null, true))).toEqual([]);
  });

  it("for a member who holds nothing: still their own pages, which no grant gates", () => {
    expect(accountLinks(visibleSections([], false)).map((l) => l.href)).toEqual([
      "/people/me",
      MY_ACCESS.href,
      "/settings/appearance",
    ]);
  });
});

/**
 * The sidebar foot's one menu (owner, 2026-10-14): the accounts, then You,
 * then Settings, then sign-out, then our signature, from ONE list. These fail
 * on the wrong builds that look right: Settings missing, the order changed,
 * an account act shown with the switcher off, "Sign out of all accounts" with
 * one account, or a signature that is a control or is out of place.
 */
describe("accountMenu: the account, You, Settings and sign-out, from one list", () => {
  const rows = (g: ReturnType<typeof accountMenu>) =>
    g.map((x) => [x.id, x.items.map((i) => (i.kind === "link" ? i.href : i.kind === "action" ? i.id : i.kind))]);
  const member = visibleSections(ALL, false);

  it("with three accounts: the current one, the others and Add, You, Settings, then both sign-outs", () => {
    expect(rows(accountMenu(member, { layout: true, switcher: true, others: 2 }))).toEqual([
      ["current", ["current"]],
      ["accounts", ["others", "add-account"]],
      ["you", ["/people/me", "/people/access"]],
      ["settings", ["/settings/appearance", "colour-mode", "change-layout"]],
      ["sign-out", ["sign-out", "sign-out-all"]],
      ["brand", ["brand"]],
    ]);
  });

  it("with one account: Add, and one Sign out", () => {
    const g = accountMenu(member, { layout: true, switcher: true, others: 0 });
    expect(rows(g).find(([id]) => id === "accounts")).toEqual(["accounts", ["add-account"]]);
    expect(rows(g).at(-2)).toEqual(["sign-out", ["sign-out"]]);
  });

  it("with the switcher flag off: the header, You, Settings and one Sign out, whatever the count", () => {
    expect(rows(accountMenu(member, { layout: true, switcher: false, others: 3 }))).toEqual([
      ["current", ["current"]],
      ["you", ["/people/me", "/people/access"]],
      ["settings", ["/settings/appearance", "colour-mode", "change-layout"]],
      ["sign-out", ["sign-out"]],
      ["brand", ["brand"]],
    ]);
  });

  it("drops Change my layout with the shell nav off", () => {
    const g = accountMenu(member, { layout: false, switcher: true, others: 0 });
    expect(g.flatMap((x) => x.items)).not.toContainEqual(CHANGE_LAYOUT_ITEM);
  });

  it("with no sections (the shell nav off, or an unresolved viewer): no You, and Settings is the colour mode", () => {
    expect(rows(accountMenu([], { layout: false, switcher: true, others: 1 }))).toEqual([
      ["current", ["current"]],
      ["accounts", ["others", "add-account"]],
      ["settings", ["colour-mode"]],
      ["sign-out", ["sign-out", "sign-out-all"]],
      ["brand", ["brand"]],
    ]);
    expect(rows(accountMenu(visibleSections(null, false), { layout: false, switcher: false, others: 0 }))).toEqual([
      ["current", ["current"]],
      ["settings", ["colour-mode"]],
      ["sign-out", ["sign-out"]],
      ["brand", ["brand"]],
    ]);
  });

  it("pages: false (no organization) keeps the account acts and drops You, Settings and the signature", () => {
    expect(rows(accountMenu(member, { layout: true, switcher: true, others: 2, pages: false }))).toEqual([
      ["current", ["current"]],
      ["accounts", ["others", "add-account"]],
      ["sign-out", ["sign-out", "sign-out-all"]],
    ]);
  });

  it("captions You and Settings, and no other group", () => {
    const g = accountMenu(member, { layout: true, switcher: true, others: 2 });
    expect(g.map((x) => x.label)).toEqual([null, null, "You", "Settings", null, null]);
  });

  // Owner, 2026-10-14: our comet and "Metorite" sign the foot of the menu.
  describe("the signature", () => {
    const shapes = [
      { layout: true, switcher: true, others: 2 },
      { layout: true, switcher: true, others: 0 },
      { layout: false, switcher: false, others: 0 },
    ];

    it("is the last group, after sign-out, in every shape", () => {
      for (const o of shapes) {
        for (const sections of [member, []]) {
          const ids = accountMenu(sections, o).map((x) => x.id);
          expect(ids.at(-1)).toBe("brand");
          expect(ids.at(-2)).toBe("sign-out");
          expect(ids.filter((id) => id === "brand")).toHaveLength(1);
        }
      }
    });

    it("holds the brand row, with no caption, and no item with an href or an action", () => {
      const brand = accountMenu(member, shapes[0]).find((x) => x.id === "brand");
      expect(brand).toEqual({ id: "brand", label: null, items: [{ kind: "brand" }] });
      for (const item of brand?.items ?? []) {
        expect(item).not.toHaveProperty("href");
        expect(item.kind).not.toMatch(/^(link|action|sign-out|sign-out-all|add-account|colour-mode|others)$/);
      }
    });

    it("is absent with pages: false, and nowhere else in the list", () => {
      for (const o of shapes) {
        const g = accountMenu(member, { ...o, pages: false });
        expect(g.map((x) => x.id)).not.toContain("brand");
        expect(g.flatMap((x) => x.items).map((i) => i.kind)).not.toContain("brand");
      }
      const full = accountMenu(member, shapes[0]);
      expect(full.slice(0, -1).flatMap((x) => x.items).map((i) => i.kind)).not.toContain("brand");
    });
  });
});

describe("All apps lists every app the member holds, never a preference", () => {
  it("for an admin: every held app, Organisation too, and no setting", () => {
    expect(shape(launcherGroups(visibleSections(ALL, true)))).toEqual([
      ["personal", ["/tasks", "/calendar", "/email", "/whatsapp", "/artifacts"]],
      ["across", ["/projects", "/crm", "/people"]],
      ["studio", ["/chat"]],
      ["admin", ["/approvals", "/settings/organization", "/integrations"]],
    ]);
  });

  it("for a member who holds nothing: Integrations only (D-EA-9)", () => {
    const groups = launcherGroups(visibleSections([], false));
    expect(groups.flatMap((g) => g.items.map((p) => p.href))).toEqual(["/integrations"]);
  });

  it("gives every app it shows a purpose to print", () => {
    const shown = launcherGroups(visibleSections(ALL, true)).flatMap((g) => g.items);
    expect(shown.filter((p) => !p.blurb).map((p) => p.href)).toEqual([]);
  });
});

describe("the first item is Home, or My Day with that flag on (NS-3)", () => {
  it("keeps the name Home while My Day is off", () => {
    expect(homePane(false)).toBe(HOME_PANE);
    expect(homePane(false).label).toBe("Home");
  });

  it("names it My Day, at the same place, with the flag on", () => {
    const pane = homePane(true);
    expect(pane.label).toBe("My Day");
    expect(pane.href).toBe("/");
    expect(pane.icon).toBe(HOME_PANE.icon);
  });

  it("reads the flag itself when no answer is given, and it is off by default", () => {
    expect(homePane().label).toBe("Home");
  });
});

describe("which item is the current one", () => {
  it("matches Home on Home only", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/tasks", "/")).toBe(false);
  });

  it("matches a segment, never a prefix of another route", () => {
    expect(isActive("/people/access", "/people")).toBe(true);
    expect(isActive("/peoplex", "/people")).toBe(false);
    expect(isActive(null, "/people")).toBe(false);
  });
});

describe("the desktop frame (owner, 2026-10-09: one bar across the top)", () => {
  it("with no shell bar: the classic frame, whatever the nav flag says", () => {
    expect(desktopFrame(false, false)).toBe("classic");
    expect(desktopFrame(false, true)).toBe("classic");
  });

  it("with the bar and no shell nav: the bar over the page column, as NS-1 built it", () => {
    expect(desktopFrame(true, false)).toBe("column");
  });

  it("with both flags: one bar across the whole width, which carries the logo", () => {
    expect(desktopFrame(true, true)).toBe("full");
  });
});

describe("the star model: each category lists only its starred apps (owner, 2026-10-11)", () => {
  const all = visibleSections(ALL, true);
  // Every pane known, as after a star-model save, so the stars filter.
  const KNOWN = PANES.map((p) => p.href);
  const starred = (pins: string[]) => ({ pins, known: KNOWN });

  it("draws only the starred apps, in nav.ts order and never in star order", () => {
    const groups = shellSidebar(all, starred(["/chat", "/projects", "/email"]));
    expect(shape(groups)).toEqual([
      ["personal", ["/email"]],
      ["across", ["/projects"]],
      ["studio", ["/chat"]],
    ]);
  });

  it("puts Personal Center first, and has no separate group of pins", () => {
    const groups = shellSidebar(all, starred(["/approvals", "/chat", "/tasks"]));
    expect(groups.map((g) => g.id)).toEqual(["personal", "studio", "admin"]);
    expect(groups.map((g) => g.label)).not.toContain("My apps");
  });

  it("hides a category with no starred app, and keeps every other one", () => {
    const groups = shellSidebar(all, starred(["/tasks", "/approvals"]));
    expect(groups.map((g) => g.id)).toEqual(["personal", "admin"]);
  });

  it("keeps My Email in Personal Center: nothing moves between categories", () => {
    const groups = shellSidebar(all, starred(["/email", "/projects"]));
    expect(groups.find((g) => g.id === "personal")!.items.map((p) => p.href)).toEqual(["/email"]);
  });

  it("draws no star the member does not hold", () => {
    const groups = shellSidebar(visibleSections(["tasks"], false), starred(["/projects", "/crm", "/tasks"]));
    expect(shape(groups)).toEqual([["personal", ["/tasks"]]]);
  });

  it("with nothing starred, draws no category at all (Home stays, apart from the groups)", () => {
    const allKnown = { pins: [], known: [...new Set(all.flatMap((s) => s.items.map((p) => p.href)))] };
    expect(shellSidebar(all, allKnown)).toEqual([]);
    expect(homePane(false).href).toBe("/");
  });

  it("without stars, or with no stored list, keeps the shape it had", () => {
    expect(shellSidebar(all, undefined)).toEqual(shellSidebar(all));
    expect(shellSidebar(all, null)).toEqual(shellSidebar(all));
    expect(shellSidebar(all, { pins: null, known: null })).toEqual(shellSidebar(all));
  });
});

describe("a run on a hidden app still shows (strayCount)", () => {
  it("counts only the runs on panes the sidebar does not show", () => {
    const shown = new Set(["/tasks", "/projects"]);
    // Chat is hidden too, so a run folded onto Chat counts here.
    expect(strayCount({ "/tasks": 2, "/chat": 1, "/email": 3 }, shown)).toBe(4);
    expect(strayCount({ "/tasks": 2 }, shown)).toBe(0);
    expect(strayCount({}, shown)).toBe(0);
  });
});
