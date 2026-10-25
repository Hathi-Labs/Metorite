/**
 * WS-64 OP-4 — the `/tasks` redirect table, the phone tab bar and the link
 * helper (`one_projects_app.md` §9 OP-4, done-when 1).
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { projectsPhoneTabs } from "./phoneTabs";
import { tasksLink, tasksRedirectTarget } from "./tasksRedirect";

describe("tasksRedirectTarget, flag on", () => {
  const to = (search: string, hash = "") => tasksRedirectTarget({ search, hash }, true);

  it("/tasks goes to Today", () => {
    expect(to("")).toBe("/projects?app=my-today");
  });
  it("/tasks#today goes to Today", () => {
    expect(to("", "#today")).toBe("/projects?app=my-today");
  });
  it("/tasks?do=capture goes to the Inbox with capture open", () => {
    expect(to("?do=capture")).toBe("/projects?app=my-inbox&do=capture");
  });
  it("/tasks?task=<id> and ?item=<id> go to the task", () => {
    expect(to("?task=abc-1")).toBe("/projects?task=abc-1");
    expect(to("?item=abc-1")).toBe("/projects?task=abc-1");
  });
  it("a task id wins over a view, and is encoded", () => {
    expect(to("?item=a%20b&do=capture")).toBe("/projects?task=a%20b");
  });
  it("capture keeps every NS-4b fill.* key", () => {
    expect(to("?do=capture&fill.title=call+the+vendor")).toBe(
      "/projects?app=my-inbox&do=capture&fill.title=call+the+vendor",
    );
  });
  it("unknown query keys and another fragment pass through", () => {
    expect(to("?x=1")).toBe("/projects?app=my-today&x=1");
    expect(to("?task=t1&x=1", "#c2")).toBe("/projects?task=t1&x=1#c2");
    expect(to("", "#today")).toBe("/projects?app=my-today");
  });
  it("another shell job stays on /tasks", () => {
    expect(to("?do=open-chat&fill.session=s1")).toBeNull();
  });
});

describe("tasksRedirectTarget, flag off", () => {
  it("never redirects", () => {
    for (const search of ["", "?do=capture", "?task=x", "?item=x"]) {
      expect(tasksRedirectTarget({ search, hash: "#today" }, false), search).toBeNull();
    }
  });
});

describe("tasksLink", () => {
  it("repoints a /tasks link when the flag is on", () => {
    expect(tasksLink("/tasks", true)).toBe("/projects?app=my-today");
    expect(tasksLink("/tasks?do=capture", true)).toBe("/projects?app=my-inbox&do=capture");
    expect(tasksLink("/tasks?item=t1", true)).toBe("/projects?task=t1");
  });
  it("keeps every link when the flag is off", () => {
    expect(tasksLink("/tasks", false)).toBe("/tasks");
    expect(tasksLink("/tasks?item=t1", false)).toBe("/tasks?item=t1");
  });
  it("keeps a link that does not name /tasks", () => {
    expect(tasksLink("/tasks-extra", true)).toBe("/tasks-extra");
    expect(tasksLink("/projects", true)).toBe("/projects");
    expect(tasksLink("/tasks?do=open-chat&fill.session=s", true)).toBe(
      "/tasks?do=open-chat&fill.session=s",
    );
  });
  it("defaults to the build flag, which is off in the test run", () => {
    expect(tasksLink("/tasks")).toBe("/tasks");
  });
});

describe("projectsPhoneTabs", () => {
  it("flag off: Projects, Views, Search", () => {
    expect(projectsPhoneTabs(false).map((t) => [t.label, t.event])).toEqual([
      ["Projects", "projects-tree"],
      ["Views", "projects-views"],
      ["Search", "projects-search"],
    ]);
  });
  it("flag on: My work, Capture, Search (Menu and Ask are the shell's)", () => {
    expect(projectsPhoneTabs(true).map((t) => [t.label, t.event])).toEqual([
      ["My work", "projects-mywork"],
      ["Capture", "projects-capture"],
      ["Search", "projects-search"],
    ]);
  });
  it("the page answers every event, and the shell draws the bar from this list", () => {
    const root = path.resolve(__dirname, "..", "..", "..");
    const page = fs.readFileSync(path.join(root, "app", "projects", "page.tsx"), "utf-8");
    for (const flag of [false, true]) {
      for (const tab of projectsPhoneTabs(flag)) {
        expect(page, tab.event).toContain(`"${tab.event}"`);
      }
    }
    const shell = fs.readFileSync(path.join(root, "components", "AppShell.tsx"), "utf-8");
    expect(shell).toContain("projectsPhoneTabs(myWorkEnabled())");
    expect(shell).not.toContain("projectsMenuEvent");
    // "My work" opens the Projects sheet, the one the "Projects" tab opens.
    expect(page).toMatch(/tab === "projects-mywork"\) \{[^}]*setSheet\(\(s\) => \(s === "tree"/);
  });
  it("the /tasks page redirects through the one function", () => {
    const root = path.resolve(__dirname, "..", "..", "..");
    const page = fs.readFileSync(path.join(root, "app", "tasks", "page.tsx"), "utf-8");
    expect(page).toContain("tasksRedirectTarget(");
  });
});
