/**
 * WS-64 OP-4 — the `/tasks` redirect table, the phone tab bar and the link
 * helper (`one_projects_app.md` §9 OP-4, done-when 1).
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { projectsPhoneTabs } from "./phoneTabs";
import { tasksLink, tasksRedirectFor, tasksRedirectTarget } from "./tasksRedirect";

describe("tasksRedirectTarget, flag on", () => {
  const to = (search: string, hash = "") => tasksRedirectTarget({ search, hash }, true);

  it("/tasks goes to Today", () => {
    expect(to("")).toBe("/projects?app=my-tasks&chip=today");
  });
  it("/tasks#today goes to Today", () => {
    expect(to("", "#today")).toBe("/projects?app=my-tasks&chip=today");
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
    expect(to("?x=1")).toBe("/projects?app=my-tasks&chip=today&x=1");
    expect(to("?task=t1&x=1", "#c2")).toBe("/projects?task=t1&x=1#c2");
    expect(to("", "#today")).toBe("/projects?app=my-tasks&chip=today");
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
    expect(tasksLink("/tasks", true)).toBe("/projects?app=my-tasks&chip=today");
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
  it("flag on: Projects, Capture, Search (Menu and Ask are the shell's)", () => {
    // MOB-10 (task 2003): the first tab opens the Projects sheet at once, so a
    // phone reaches the whole company. My tasks is a row in that sheet.
    expect(projectsPhoneTabs(true).map((t) => [t.label, t.event])).toEqual([
      ["Projects", "projects-tree"],
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
    // MOB-10: no tab sends the old "My tasks" event, so the page has no handler for it.
    expect(page).not.toContain("projects-mywork");
    expect(page).not.toContain("phoneMyTasksTap");
    // Each Projects tab closes the page's other overlays first, so two never stack.
    const handler = page.slice(page.indexOf('tab === "projects-tree"'), page.indexOf('window.addEventListener("cc-mobile-nav"'));
    expect(handler).toMatch(/setSearching\(false\)[\s\S]*closeQuickCapture\(\)[\s\S]*setSheet\(\(s\) => \(s === which/);
    expect(handler).toMatch(/projects-capture[\s\S]*setSheet\(null\)[\s\S]*setSearching\(false\)[\s\S]*openQuickCapture/);
    expect(handler).toMatch(/projects-search[\s\S]*setSheet\(null\)[\s\S]*closeQuickCapture\(\)[\s\S]*setSearching\(true\)/);
  });
  it("the /tasks page redirects through the one function", () => {
    const root = path.resolve(__dirname, "..", "..", "..");
    const page = fs.readFileSync(path.join(root, "app", "tasks", "page.tsx"), "utf-8");
    expect(page).toContain("tasksRedirectFor(");
  });
});

describe("tasksRedirectFor, the member may not hold Projects", () => {
  const url = { search: "", hash: "" };
  it("redirects when the flag is on and Projects is held", () => {
    expect(tasksRedirectFor(url, true, true)).toBe("/projects?app=my-tasks&chip=today");
  });
  it("stays when the flag is on and Projects is not held", () => {
    expect(tasksRedirectFor(url, true, false)).toBeNull();
    expect(tasksRedirectFor({ search: "?do=capture", hash: "" }, true, false)).toBeNull();
  });
  it("stays when the flag is off", () => {
    expect(tasksRedirectFor(url, false, true)).toBeNull();
    expect(tasksRedirectFor(url, false, false)).toBeNull();
  });
});
