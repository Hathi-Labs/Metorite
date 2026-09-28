/**
 * WS-41 I-4 — the import wizard's pure logic.
 * Spec: project-docs/specs/project_import.md §7.4, §7.5, §7.7.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { Access } from "@/lib/access";

import {
  IMPORT_PERMISSION,
  type ImportRun,
  MAX_UPLOAD_BYTES,
  canImport,
  importEnabled,
  continuationNote,
  isTerminal,
  mappingFrom,
  mustConfirmNewTree,
  progressOf,
  reportLines,
  uploadProblem,
} from "./importFlow";

const access = (capabilities: string[]): Access =>
  ({ capabilities, features: [], is_admin: false }) as unknown as Access;

describe("the flag", () => {
  it.each(["1", "true", "on", "yes", " ON "])("%s turns it on", (v) => {
    expect(importEnabled({ NEXT_PUBLIC_PROJECTS_IMPORT: v })).toBe(true);
  });
  it.each(["", "0", "off", "false", undefined])("%s leaves it off", (v) => {
    expect(importEnabled({ NEXT_PUBLIC_PROJECTS_IMPORT: v })).toBe(false);
  });
  it("is read LITERALLY, so the browser bundle inlines it", () => {
    const src = readFileSync(join(__dirname, "importFlow.ts"), "utf-8");
    expect(src).toContain("process.env.NEXT_PUBLIC_PROJECTS_IMPORT");
  });
  it("the page reads it once, at module scope", () => {
    const page = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");
    expect(page).toMatch(/^const IMPORT_LIVE = importEnabled\(\);$/m);
  });
});

describe("who sees the entry", () => {
  it("an admin with the permission, once access has loaded", () => {
    expect(canImport(access([IMPORT_PERMISSION]), false)).toBe(true);
  });
  it("nobody while access loads, so it never flashes", () => {
    expect(canImport(access([IMPORT_PERMISSION]), true)).toBe(false);
  });
  it("not a member without it", () => {
    expect(canImport(access(["projects:read"]), false)).toBe(false);
  });
  it("names the permission the gateway checks", () => {
    expect(IMPORT_PERMISSION).toBe("admin:access:manage");
  });
});

describe("the upload", () => {
  const file = (name: string, size: number) => ({ name, size });
  it("needs a file", () => {
    expect(uploadProblem([])).toMatch(/Choose/);
  });
  it("takes a CSV under the limit", () => {
    expect(uploadProblem([file("export.csv", 1_159_110)])).toBeNull();
  });
  it("refuses more than the proxy would pass whole", () => {
    expect(uploadProblem([file("a.csv", MAX_UPLOAD_BYTES), file("b.csv", 1)])).toMatch(/one space at a time/);
  });
  it("stays under the 10 MB the Next proxy buffers", () => {
    expect(MAX_UPLOAD_BYTES).toBeLessThan(10 * 1024 * 1024);
  });
  it("refuses a file that is not a CSV", () => {
    expect(uploadProblem([file("export.xlsx", 10)])).toMatch(/not a CSV/);
  });
});

const run = (over: Partial<ImportRun> = {}): ImportRun =>
  ({
    id: "r",
    source: "clickup",
    state: "planned",
    created_by: "a@x.test",
    created_at: null,
    files: [],
    mapping: { people: {}, statuses: {}, target: { kind: "new_space" }, grant: "org" },
    plan: {
      summary: { tasks: 2423, subtasks: 1270, spaces: 5, folders: 9, projects: 48, people: 2, comments: 93, rows_read: 2689 },
      warnings: [],
      losses: [],
      people: [
        { ref: "name:ann", display_name: "Ann", email: null, tasks: 3, comments: 0, proposed: "ann@x.test", match: "name", member: "ann@x.test" },
        { ref: "name:bo", display_name: "Bo", email: null, tasks: 1, comments: 0, proposed: null, match: null, member: null },
      ],
      statuses: [
        { name: "to do", tasks: 5, lists: 1, proposed: "todo", category: "todo", becomes: "to do" },
        { name: "Closed", tasks: 9, lists: 2, proposed: "done", category: "done", becomes: "Closed" },
      ],
      closed_tasks: 9,
      completed_at_estimated: 9,
      done_status_added: 0,
      skip: { written_by_old_importer: 0, total: 0 },
      to_update: 0,
      to_write: { tasks: 2423, comments: 93 },
      target: { kind: "new_space", name: null, project_id: null },
      grant: "org",
      errors: [],
      ready: true,
    },
    ...over,
  }) as ImportRun;

describe("the mapping the admin's edits make", () => {
  it("keeps the server's proposals where the admin changed nothing", () => {
    const m = mappingFrom(run(), {}, {}, { kind: "new_space", name: null });
    expect(m.people).toEqual({ "name:ann": "ann@x.test", "name:bo": null });
    expect(m.statuses["Closed"]).toEqual({ category: "done", name: null });
  });
  it("takes an edit, including a deliberate unassign", () => {
    const m = mappingFrom(run(), { "name:ann": null, "name:bo": "bo@x.test" }, { Closed: "cancelled" }, {
      kind: "existing",
      project_id: "p",
    });
    expect(m.people).toEqual({ "name:ann": null, "name:bo": "bo@x.test" });
    expect(m.statuses["Closed"].category).toBe("cancelled");
    expect(m.target).toEqual({ kind: "existing", project_id: "p" });
  });
});

describe("progress and the report", () => {
  it("follows the writer's cursor", () => {
    expect(progressOf(run({ state: "applying", progress: { cursor: 600 } }))).toEqual({
      done: 600,
      total: 2423,
      percent: 24,
    });
  });
  it("reads 100 once done, whatever the cursor says", () => {
    expect(progressOf(run({ state: "done", progress: { cursor: 0 } })).percent).toBe(100);
  });
  it("knows which states end the wait", () => {
    expect(["done", "failed", "discarded"].every((s) => isTerminal(s as ImportRun["state"]))).toBe(true);
    expect(isTerminal("applying")).toBe(false);
  });
  it("says what the import did, in words", () => {
    const lines = reportLines({
      created: { spaces: 5, folders: 9, projects: 48 },
      tasks_written: 2423,
      tasks_updated: 2,
      conflicts_kept: 1,
      comments_written: 93,
      people_unassigned: 1,
    });
    expect(lines).toEqual([
      "Created 5 spaces, 9 folders, 48 projects.",
      "Wrote 2,423 new tasks.",
      "Updated 2 tasks that changed in ClickUp.",
      "Kept 1 edit made in Metorite over ClickUp's change.",
      "Added 93 comments.",
      "1 person had no member, so their tasks are unassigned.",
    ]);
  });
});

describe("an export an earlier import already brought in", () => {
  const plan = (over: Partial<ImportRun["plan"]>) => ({ ...run().plan, ...over });
  it("says nothing when there was no earlier import", () => {
    expect(continuationNote(plan({ inherited_from: null }))).toBeNull();
    expect(mustConfirmNewTree(plan({ inherited_from: null }), false)).toBe(false);
  });
  it("says it continues in the same spaces while the choices hold", () => {
    const p = plan({ inherited_from: "r0", continues: true });
    expect(continuationNote(p)).toMatch(/same spaces/);
    expect(mustConfirmNewTree(p, false)).toBe(false);
  });
  it("says a changed destination starts a new tree, and stops once for it", () => {
    const p = plan({ inherited_from: "r0", continues: false });
    expect(continuationNote(p)).toMatch(/new tree/);
    expect(mustConfirmNewTree(p, false)).toBe(true);
    expect(mustConfirmNewTree(p, true)).toBe(false);
  });
  it("reports statuses added to lists that were already there", () => {
    expect(reportLines({ lanes_added: 2 })).toEqual(["Added 2 statuses to lists that were already in Metorite."]);
    expect(reportLines({ lanes_added: 1 })).toEqual(["Added 1 status to lists that were already in Metorite."]);
  });
});

describe("the dialog opens on a phone too", () => {
  it("is mounted in `overlays`, which both returns render", () => {
    // The visual rig found this: mounted beside MoveDialog, it opened nothing
    // on a phone (H-120 is the same defect for "Move to…").
    const page = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");
    const overlays = page.slice(page.indexOf("const overlays = ("), page.indexOf("if (isMobile) {"));
    expect(overlays).toContain("<ImportDialog");
    expect(page.split("<ImportDialog").length - 1).toBe(1);
  });
});
