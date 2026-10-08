/**
 * The shell nav (NS-2): the sidebar, the account menu and All apps, read
 * from the manifest. Each case names the member it is about, because what a
 * member sees is the thing under test.
 */
import { describe, expect, it } from "vitest";

import { PANES, visibleSections, type NavPane, type NavSection } from "@/lib/nav";
import {
  MY_ACCESS,
  TEAM_GROUPS,
  accountLinks,
  isActive,
  launcherGroups,
  shellSidebar,
} from "./shellNav";

const ALL = PANES.map((p) => p.feature).filter((f): f is string => !!f);
const shape = (sections: NavSection[]) => sections.map((s) => [s.id, s.items.map((p) => p.href)]);

describe("the sidebar holds places to work, by team", () => {
  it("for a fully granted admin: four groups, and no page about the member", () => {
    expect(shape(shellSidebar(visibleSections(ALL, true)))).toEqual([
      ["personal", ["/tasks", "/calendar", "/email", "/whatsapp"]],
      ["across", ["/projects", "/people"]],
      ["studio", ["/chat"]],
      ["admin", ["/approvals"]],
    ]);
  });

  it("keeps Chat and Approvals in the sidebar until NS-6 builds the dock and the bell", () => {
    const hrefs = shellSidebar(visibleSections(ALL, true)).flatMap((s) => s.items.map((p) => p.href));
    expect(hrefs).toContain("/chat");
    expect(hrefs).toContain("/approvals");
  });

  it("for a member with tasks and email only: one group, and no empty heading", () => {
    expect(shape(shellSidebar(visibleSections(["tasks", "email"], false)))).toEqual([
      ["personal", ["/tasks", "/calendar", "/email"]],
    ]);
  });

  it("for a member who holds nothing: no group at all", () => {
    expect(shellSidebar(visibleSections([], false))).toEqual([]);
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
  it("for an admin: My Profile, My access, Appearance, then Organisation", () => {
    expect(accountLinks(visibleSections(ALL, true)).map((l) => l.label)).toEqual([
      "My Profile",
      "My access",
      "Appearance",
      "Organisation",
    ]);
  });

  it("for a member who is not an admin: no Organisation", () => {
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

describe("All apps lists every app the member holds, never a preference", () => {
  it("for an admin: every held app, Organisation too, and no setting", () => {
    expect(shape(launcherGroups(visibleSections(ALL, true)))).toEqual([
      ["personal", ["/tasks", "/calendar", "/email", "/whatsapp"]],
      ["across", ["/projects", "/people"]],
      ["studio", ["/chat"]],
      ["admin", ["/approvals", "/settings/organization"]],
    ]);
  });

  it("for a member who holds nothing: nothing", () => {
    expect(launcherGroups(visibleSections([], false))).toEqual([]);
  });

  it("gives every app it shows a purpose to print", () => {
    const shown = launcherGroups(visibleSections(ALL, true)).flatMap((g) => g.items);
    expect(shown.filter((p) => !p.blurb).map((p) => p.href)).toEqual([]);
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
