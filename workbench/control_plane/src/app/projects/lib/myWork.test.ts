/**
 * Projects · My work (WS-64 OP-2, `one_projects_app.md` §9 OP-2).
 *
 * Fences: the flag, the six entries, the counts that equal `/tasks`, the
 * `?app=` keys, the landing rule, and the two source rules (no copy of a
 * `/tasks` component, no import of its page).
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { inboxCount } from "@/app/tasks/lib/inbox";
import { viewCounts } from "@/app/tasks/lib/taskStore";
import type { MyTask } from "@/app/tasks/lib/types";

import {
  MY_WORK_VIEWS,
  isMyWorkId,
  landingApp,
  myWorkCounts,
  myWorkEnabled,
  myWorkView,
} from "./myWork";
import { MY_WORK_SECTION_LABEL, PROJECT_APP_SECTIONS, projectAppSections, resolveAppLink } from "./projectApps";

const ON = { NEXT_PUBLIC_PROJECTS_MY_WORK: "1" };
const OFF = { NEXT_PUBLIC_PROJECTS_MY_WORK: "0" };
const KEYS = ["my-inbox", "my-today", "my-next", "my-waiting", "my-someday", "my-done"];

describe("myWorkEnabled", () => {
  it("is off by default and on for the four spellings", () => {
    expect(myWorkEnabled({})).toBe(false);
    expect(myWorkEnabled(OFF)).toBe(false);
    for (const raw of ["1", "true", "on", "yes"]) {
      expect(myWorkEnabled({ NEXT_PUBLIC_PROJECTS_MY_WORK: raw }), raw).toBe(true);
    }
  });

  it("reads the literal member expression that Next inlines", () => {
    const source = fs.readFileSync(path.join(__dirname, "myWork.ts"), "utf8");
    expect(source).toContain("process.env.NEXT_PUBLIC_PROJECTS_MY_WORK");
    expect(source).not.toMatch(/env: Record<string, string \| undefined> = process\.env/);
  });
});

describe("the My work section", () => {
  it("is absent with the flag off, and the list is the shape of record", () => {
    const sections = projectAppSections(OFF);
    expect(sections.map((s) => s.id)).toEqual(PROJECT_APP_SECTIONS.map((s) => s.id));
    expect(sections.flatMap((s) => s.items.map((i) => i.id as string))).not.toContain("my-today");
  });

  it("shows six live entries, first, with the flag on", () => {
    const sections = projectAppSections(ON);
    expect(sections[0].id).toBe("my-work");
    expect(sections[0].label).toBe(MY_WORK_SECTION_LABEL);
    expect(MY_WORK_SECTION_LABEL).toBe("My work");
    expect(sections[0].items.map((i) => i.id)).toEqual(KEYS);
    expect(sections[0].items.every((i) => i.launch === "live")).toBe(true);
    // The rest of the list does not change.
    expect(sections.slice(1).map((s) => s.id)).toEqual(PROJECT_APP_SECTIONS.map((s) => s.id));
  });

  it("names the six views in the order of the spec", () => {
    expect(MY_WORK_VIEWS.map((v) => v.label)).toEqual([
      "Inbox",
      "Today",
      "Next actions",
      "Waiting for",
      "Someday / Maybe",
      "Done",
    ]);
  });
});

describe("resolveAppLink", () => {
  it("opens each key with the flag on", () => {
    const sections = projectAppSections(ON);
    for (const key of KEYS) expect(resolveAppLink(key, sections), key).toBe(key);
    expect(resolveAppLink(" My-Today ", sections)).toBe("my-today");
  });

  it("refuses each key with the flag off", () => {
    const sections = projectAppSections(OFF);
    for (const key of KEYS) expect(resolveAppLink(key, sections), key).toBeNull();
  });

  it("knows which keys draw a store view", () => {
    expect(KEYS.every((k) => isMyWorkId(k))).toBe(true);
    expect(isMyWorkId("reports")).toBe(false);
    expect(myWorkView("my-today")).toBe("today");
    expect(myWorkView("my-next")).toBe("next");
    expect(myWorkView("settings")).toBeNull();
  });
});

describe("the landing rule", () => {
  it("opens Today with the flag on and no link", () => {
    expect(landingApp(true, {})).toBe("my-today");
    expect(landingApp(true, { project: null, task: null, app: null })).toBe("my-today");
  });

  it("lets every link win", () => {
    expect(landingApp(true, { project: "p1" })).toBeNull();
    expect(landingApp(true, { task: "t1" })).toBeNull();
    expect(landingApp(true, { app: "reports" })).toBeNull();
  });

  it("changes nothing with the flag off", () => {
    expect(landingApp(false, {})).toBeNull();
  });
});

describe("the counts", () => {
  const task = (id: string, over: Partial<MyTask> = {}): MyTask => ({
    id,
    source: "LOCAL",
    title: id,
    disposition: "NEXT",
    isMine: true,
    createdAt: "2026-10-01T09:00:00Z",
    updatedAt: "2026-10-01T09:00:00Z",
    ...over,
  });
  const ITEMS: MyTask[] = [
    task("i1", { disposition: "INBOX" }),
    task("i2", { disposition: "INBOX" }),
    task("b1", { disposition: "NEXT" }),
    task("n1", { dueAt: "2026-10-24" }),
    task("n2", { dueAt: "2026-10-01" }),
    task("n3"),
    task("w1", { disposition: "WAITING" }),
    task("s1", { disposition: "SOMEDAY" }),
    task("d1", { disposition: "DONE" }),
    task("a1", { archivedAt: "2026-10-02T00:00:00Z" }),
  ];
  const FROM = new Set(["b1"]);

  it("each count is the one that /tasks draws", () => {
    const mine = myWorkCounts(ITEMS, FROM, "2026-10-24");
    const tasks = viewCounts(ITEMS);
    expect(mine["my-inbox"]).toBe(inboxCount(ITEMS, FROM));
    expect(mine["my-next"]).toBe(tasks.next);
    expect(mine["my-waiting"]).toBe(tasks.waiting);
    expect(mine["my-someday"]).toBe(tasks.someday);
    expect(mine["my-done"]).toBe(tasks.done);
    expect(mine["my-today"]).toBe(2);
  });

  it("is built from the two functions of /tasks, with no copy", () => {
    const source = fs.readFileSync(path.join(__dirname, "myWork.ts"), "utf8");
    expect(source).toMatch(/viewCounts\(/);
    expect(source).toMatch(/inboxCount\(/);
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
  it("no file under app/projects has the basename of a file in app/tasks/components", () => {
    const taken = new Set(fs.readdirSync(TASK_COMPONENTS).map((f) => f.toLowerCase()));
    const clash = walk(PROJECTS)
      .map((f) => path.basename(f))
      .filter((name) => taken.has(name.toLowerCase()));
    expect(clash, "a My work view is imported from app/tasks/components, never copied").toEqual([]);
  });

  it("nothing imports app/tasks/page.tsx", () => {
    const importer = /from\s+["'][^"']*tasks\/page["']/;
    const offenders = walk(path.join(APP, "..")).filter(
      (f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f) && importer.test(fs.readFileSync(f, "utf8")),
    );
    expect(offenders.map((f) => path.relative(APP, f))).toEqual([]);
  });

  it("the host imports the views by path from app/tasks/components", () => {
    const source = fs.readFileSync(path.join(PROJECTS, "components", "MyWorkHost.tsx"), "utf8");
    for (const view of ["InboxView", "ItemList"]) {
      expect(source).toContain(`@/app/tasks/components/${view}`);
    }
    expect(source).not.toMatch(/tasks\/page/);
  });
});
