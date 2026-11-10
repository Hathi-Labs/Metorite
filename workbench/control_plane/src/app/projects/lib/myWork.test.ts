/**
 * Projects · My work (WS-64 OP-2 and OP-11, `one_projects_app.md` §9 OP-2 and
 * §9 OP-11).
 *
 * Fences: the flag, the two rows (My task inbox and My tasks), the counts
 * from the server summary, the `?app=` keys and their aliases, the landing
 * rule, and the two source rules (no copy of a `/tasks` component, no import
 * of its page).
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  MY_WORK_VIEWS,
  isMyTasksApp,
  isMyWorkId,
  landingApp,
  myWorkEnabled,
  myWorkNavCounts,
  myWorkView,
  linkKeepsApp,
  projectsUndoScope,
} from "./myWork";
import { MY_WORK_SECTION_LABEL, PROJECT_APP_SECTIONS, projectAppSections, resolveAppLink } from "./projectApps";

const ON = { NEXT_PUBLIC_PROJECTS_MY_WORK: "1" };
const OFF = { NEXT_PUBLIC_PROJECTS_MY_WORK: "0" };
const KEYS = ["my-inbox", "my-tasks"];
/** The ids of the five old lists. Each one is an alias of My tasks now (OP-11). */
const LEGACY = ["my-today", "my-next", "my-waiting", "my-someday", "my-done"];

describe("myWorkEnabled", () => {
  it("is off by default and on for the four spellings", () => {
    expect(myWorkEnabled({})).toBe(false);
    expect(myWorkEnabled(OFF)).toBe(false);
    for (const raw of ["1", "true", "on", "yes"]) {
      expect(myWorkEnabled({ NEXT_PUBLIC_PROJECTS_MY_WORK: raw }), raw).toBe(true);
    }
  });

  it("reads the literal member expression that Next inlines", () => {
    const source = fs.readFileSync(path.join(__dirname, "myWorkFlag.ts"), "utf8");
    expect(source).toContain("process.env.NEXT_PUBLIC_PROJECTS_MY_WORK");
    expect(source).not.toMatch(/env: Record<string, string \| undefined> = process\.env/);
  });
});

describe("the My work section", () => {
  it("is absent with the flag off, and the list is the shape of record", () => {
    const sections = projectAppSections(OFF);
    expect(sections.map((s) => s.id)).toEqual(PROJECT_APP_SECTIONS.map((s) => s.id));
    const ids = sections.flatMap((s) => s.items.map((i) => i.id as string));
    expect(ids).not.toContain("my-tasks");
    expect(ids).not.toContain("my-today");
  });

  it("shows two live rows, first, with the flag on", () => {
    const sections = projectAppSections(ON);
    expect(sections[0].id).toBe("my-work");
    expect(sections[0].label).toBe(MY_WORK_SECTION_LABEL);
    expect(MY_WORK_SECTION_LABEL).toBe("My work");
    expect(sections[0].items.map((i) => i.id)).toEqual(KEYS);
    expect(sections[0].items.every((i) => i.launch === "live")).toBe(true);
    // The rest of the list does not change.
    expect(sections.slice(1).map((s) => s.id)).toEqual(PROJECT_APP_SECTIONS.map((s) => s.id));
  });

  it("names the two rows My task inbox and My tasks", () => {
    expect(MY_WORK_VIEWS.map((v) => v.label)).toEqual(["My task inbox", "My tasks"]);
  });
});

describe("resolveAppLink", () => {
  it("opens each key with the flag on", () => {
    const sections = projectAppSections(ON);
    for (const key of KEYS) expect(resolveAppLink(key, sections), key).toBe(key);
    expect(resolveAppLink(" My-Tasks ", sections)).toBe("my-tasks");
  });

  it("sends each old list id to My tasks with the flag on (OP-11)", () => {
    const sections = projectAppSections(ON);
    for (const key of LEGACY) expect(resolveAppLink(key, sections), key).toBe("my-tasks");
    expect(resolveAppLink(" My-Today ", sections)).toBe("my-tasks");
  });

  it("refuses each key and each old id with the flag off", () => {
    const sections = projectAppSections(OFF);
    for (const key of [...KEYS, ...LEGACY]) expect(resolveAppLink(key, sections), key).toBeNull();
  });

  it("knows which keys draw a store view", () => {
    expect(KEYS.every((k) => isMyWorkId(k))).toBe(true);
    expect(LEGACY.some((k) => isMyWorkId(k))).toBe(false);
    expect(isMyWorkId("reports")).toBe(false);
    expect(myWorkView("my-inbox")).toBe("inbox");
    // My tasks reads the board endpoints, never the task store.
    expect(myWorkView("my-tasks")).toBeNull();
    expect(myWorkView("settings")).toBeNull();
    expect(isMyTasksApp("my-tasks")).toBe(true);
    expect(isMyTasksApp("my-inbox")).toBe(false);
  });
});

describe("the landing rule", () => {
  it("opens My tasks with the flag on and no link", () => {
    expect(landingApp(true, {})).toBe("my-tasks");
    expect(landingApp(true, { project: null, task: null, app: null })).toBe("my-tasks");
  });

  it("lets every link win", () => {
    expect(landingApp(true, { project: "p1" })).toBeNull();
    expect(landingApp(true, { task: "t1" })).toBeNull();
    expect(landingApp(true, { app: "reports" })).toBeNull();
    // The People "Assign work" link is /projects?assignee=<email>.
    expect(landingApp(true, { assignee: "a@b.co" })).toBeNull();
    expect(landingApp(true, { center: "ops" })).toBeNull();
  });

  it("changes nothing with the flag off", () => {
    expect(landingApp(false, {})).toBeNull();
  });

  it("the page puts the Today chip on the landing", () => {
    const page = fs.readFileSync(path.join(__dirname, "..", "page.tsx"), "utf8");
    expect(page).toMatch(/setApp\(to\);[\s\S]{0,200}chip: "today"/);
  });
});

describe("the counts (OP-11)", () => {
  const SUMMARY = {
    tasks: 12,
    by_category: { todo: 5, in_progress: 2, done: 4, cancelled: 1 },
    by_disposition: { inbox: 3, today: 2, next: 4, waiting: 1, someday: 1, done: 4 },
  };

  it("My task inbox shows the server inbox count, and My tasks the open tasks", () => {
    expect(myWorkNavCounts(SUMMARY)).toEqual({ "my-inbox": 3, "my-tasks": 7 });
  });

  it("shows no count before the summary lands, never a zero", () => {
    expect(myWorkNavCounts(null)).toEqual({});
    expect(myWorkNavCounts(undefined)).toEqual({});
  });

  it("reads no client counter of /tasks", () => {
    const source = fs.readFileSync(path.join(__dirname, "myWork.ts"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(source).not.toMatch(/viewCounts|inboxCount|taskStore/);
  });
});

// ── The source fences (done-when 3) ─────────────────────────────────────────

const APP = path.join(__dirname, "..", "..");
const PROJECTS = path.join(APP, "projects");
const TASK_COMPONENTS = path.join(APP, "tasks", "components");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

describe("My work is a mount, not a copy", () => {
  // Two clashes were in the tree before OP-2 (measured 2026-10-24): each is a
  // different component for a different app (the Projects chat rail and
  // board). The list is closed. Never add a name to it.
  // Pinned by full path, so a new copy of the same name elsewhere fails.
  const BEFORE_OP2 = new Set([
    "app/projects/components/assistantrail.tsx",
    "app/projects/components/taskboard.tsx",
  ]);
  const rel = (f: string) => path.relative(path.join(APP, ".."), f).split(path.sep).join("/").toLowerCase();

  it("no file under app/projects has the basename of a file in app/tasks/components", () => {
    const taken = new Set(fs.readdirSync(TASK_COMPONENTS).map((f) => f.toLowerCase()));
    const clash = walk(PROJECTS)
      .filter((f) => taken.has(path.basename(f).toLowerCase()) && !BEFORE_OP2.has(rel(f)))
      .map((f) => rel(f));
    expect(clash, "a My work view is imported from app/tasks/components, never copied").toEqual([]);
  });

  it("the closed list still names real clashes, so it cannot go stale", () => {
    const projects = new Set(walk(PROJECTS).map(rel));
    const tasks = new Set(fs.readdirSync(TASK_COMPONENTS).map((f) => f.toLowerCase()));
    for (const name of BEFORE_OP2) {
      expect(projects.has(name) && tasks.has(path.basename(name)), name).toBe(true);
    }
  });

  it("nothing imports app/tasks/page.tsx", () => {
    const importer = /from\s+["'][^"']*tasks\/page["']/;
    const offenders = walk(path.join(APP, "..")).filter(
      (f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f) && importer.test(fs.readFileSync(f, "utf8")),
    );
    expect(offenders.map((f) => path.relative(APP, f))).toEqual([]);
  });

  it("the host imports the inbox view by path from app/tasks/components", () => {
    const source = fs
      .readFileSync(path.join(PROJECTS, "components", "MyWorkHost.tsx"), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "");
    expect(source).toContain("@/app/tasks/components/InboxView");
    // OP-11: the five other lists render in My tasks, not through ItemList.
    expect(source).not.toContain("@/app/tasks/components/ItemList");
    expect(source).not.toMatch(/tasks\/page/);
  });
});

describe("My work and the shared stores (fix round 1)", () => {
  it("My work has its own undo scope, and the board scope returns after it", () => {
    // OP-11 fix round, item 12: My tasks does not share the inbox's history.
    expect(projectsUndoScope("my-tasks", "p1")).toBe("projects:my-tasks");
    expect(projectsUndoScope("my-tasks", null)).not.toBe(projectsUndoScope("my-inbox", null));
    expect(projectsUndoScope("my-inbox", null)).toBe("projects:my-work");
    expect(projectsUndoScope(null, "p1")).toBe("projects:p1");
    expect(projectsUndoScope("reports", "p1")).toBe("projects:p1");
    expect(projectsUndoScope(null, null)).toBe("projects:none");
  });

  const host = fs
    .readFileSync(path.join(PROJECTS, "components", "MyWorkHost.tsx"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");

  it("the host claims the My work undo scope", () => {
    expect(host).toContain('useUndoScope("projects:my-work")');
  });

  it("the host puts the store view back on unmount", () => {
    expect(host).toMatch(/return \(\) => \{[\s\S]*selectView\(tasksEntryView\(before\)\)/);
  });

  it("the host reads the date again when the tab returns", () => {
    // WS-64 #128: through the one live-refresh hook, which owns both events.
    expect(host).toContain("useLiveRefresh(");
    expect(host).toContain("loadMyToday()");
    const hook = fs.readFileSync(path.join(PROJECTS, "..", "..", "lib", "liveRefresh.ts"), "utf8");
    expect(hook).toContain('"visibilitychange"');
    expect(hook).toContain('"focus"');
  });

  it("OP-11 fix round 4: a capture link keeps app=my-inbox while its job waits", () => {
    const q = (s: string) => new URLSearchParams(s);
    expect(linkKeepsApp("my-inbox", q("app=my-inbox&do=capture&fill.title=x"))).toBe(true);
    expect(linkKeepsApp("my-tasks", q("app=my-tasks&do=capture"))).toBe(true);
    expect(linkKeepsApp("my-inbox", q("app=my-inbox"))).toBe(false);
    expect(linkKeepsApp("reports", q("app=reports&do=capture"))).toBe(false);
    expect(linkKeepsApp(null, q("do=capture"))).toBe(false);
    const page = fs.readFileSync(path.join(PROJECTS, "page.tsx"), "utf8");
    expect(page).toContain("if (linkKeepsApp(live, searchParams)) return;");
  });

  it("the page uses the scope rule, not a project-only scope", () => {
    const page = fs.readFileSync(path.join(PROJECTS, "page.tsx"), "utf8");
    expect(page).toContain("useUndoScope(projectsUndoScope(app, selected?.id))");
  });
});
