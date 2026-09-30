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
  discardLines,
  discardRefusal,
  runLine,
  isStalled,
  isTerminal,
  keepOnReopen,
  pollDelay,
  STALL_MS,
  mappingFrom,
  stageClashes,
  statusMerges,
  importTreeRows,
  treeTotals,
  grantOptions,
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

describe("I-8: the tree, status names, columns and sharing", () => {
  const tree = [
    { ref: "s", kind: "space" as const, name: "Space", parent_ref: null, tasks: 0 },
    { ref: "f", kind: "folder" as const, name: "Folder", parent_ref: "s", tasks: 0 },
    { ref: "l1", kind: "project" as const, name: "List 1", parent_ref: "f", tasks: 5 },
    { ref: "l2", kind: "project" as const, name: "List 2", parent_ref: "s", tasks: 7 },
  ];

  it("reads the tree parents first, with depth", () => {
    expect(importTreeRows(tree, {}).map((r) => `${r.depth}:${r.ref}`)).toEqual(["0:s", "1:f", "2:l1", "1:l2"]);
  });
  it("a skipped folder leaves out what is under it, and the totals follow", () => {
    const rows = importTreeRows(tree, { f: { skip: true } });
    const l1 = rows.find((r) => r.ref === "l1")!;
    expect(l1.skipInherited).toBe(true);
    expect(l1.skipSelf).toBe(false);
    expect(treeTotals(rows)).toEqual({ lists: 1, skippedLists: 1, tasks: 7 });
  });
  it("a rename shows the new name, and blank spaces never make a name", () => {
    const rows = importTreeRows(tree, { l2: { name: "  Launch   plan " }, l1: { name: "   " } });
    expect(rows.find((r) => r.ref === "l2")!.shownName).toBe("Launch plan");
    expect(rows.find((r) => r.ref === "l1")!.shownName).toBe("List 1");
  });

  it("two statuses given one name merge, without case", () => {
    const merges = statusMerges(run().plan.statuses, { "to do": "closed" });
    expect(merges["to do"]).toEqual(["Closed"]);
    expect(merges["Closed"]).toEqual(["to do"]);
    expect(statusMerges(run().plan.statuses, {})["Closed"]).toEqual([]);
  });

  it("marks a merge whose stages differ, and never changes a stage itself", () => {
    const statuses = run().plan.statuses;
    const merges = statusMerges(statuses, { "to do": "Closed" });
    expect([...stageClashes(statuses, merges, {})].sort()).toEqual(["Closed", "to do"]);
    expect(stageClashes(statuses, merges, { "to do": "done" }).size).toBe(0);
  });

  it("drops a container choice for a ref the file no longer holds", () => {
    const planned = run();
    planned.plan.tree = tree;
    const m = mappingFrom(planned, {}, {}, { kind: "new_space", name: null }, {
      containers: { gone: { skip: true }, l1: { skip: true } },
    });
    expect(m.containers).toEqual({ l1: { name: null, skip: true } });
  });

  it("carries the I-8 choices, and drops the ones that change nothing", () => {
    const planned = run();
    planned.plan.tree = tree;
    const m = mappingFrom(planned, {}, {}, { kind: "new_space", name: null }, {
      grant: "group:eng",
      statusNames: { Closed: "  Done ", "to do": "" },
      containers: { l1: { skip: true }, l2: { name: "  " }, f: { name: "Ops" } },
      columns: { Sprint: "description", Points: "skip" },
    });
    expect(m.grant).toBe("group:eng");
    expect(m.statuses["Closed"]).toEqual({ category: "done", name: "Done" });
    expect(m.statuses["to do"]).toEqual({ category: "todo", name: null });
    expect(m.containers).toEqual({ l1: { name: null, skip: true }, f: { name: "Ops", skip: false } });
    expect(m.columns).toEqual({ Sprint: "description" });
  });

  it("offers everyone first, then each group", () => {
    expect(grantOptions([{ slug: "eng", display_name: "Engineering" }]).map((o) => o.value)).toEqual([
      "org",
      "group:eng",
    ]);
  });
});

describe("I-8: the Map step reads the plan of the chosen tree", () => {
  it("the Spaces step's Next saves the mapping before Map shows", () => {
    const dialog = readFileSync(join(__dirname, "..", "components", "ImportDialog.tsx"), "utf-8");
    const save = dialog.slice(dialog.indexOf("const saveTree"), dialog.indexOf("const saveAndImport"));
    expect(save).toContain("importApi.saveMapping(");
    expect(save.indexOf("setRun(planned)")).toBeLessThan(save.indexOf('setStep("map")'));
    expect(dialog).toContain("onClick={() => void saveTree()}");
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
  it("says it continues when the writer would, even with no inherited mapping", () => {
    expect(continuationNote(plan({ inherited_from: null, continues: true }))).toMatch(/same spaces/);
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
    expect(reportLines({ tasks_written: 1 }, { containers: 1, tasks: 36 })).toContain(
      "Left out 36 tasks in the spaces and lists you unticked.",
    );
  });
});

describe("a run the admin cannot watch every second", () => {
  it("keeps polling after a failed poll, more slowly", () => {
    expect(pollDelay(0)).toBe(1500);
    expect(pollDelay(2)).toBe(6000);
    expect(pollDelay(20)).toBe(30_000);
  });
  it("offers Resume only after the server would call the writer dead", () => {
    const applying = run({ state: "applying" });
    expect(isStalled(applying, 0, 120_000)).toBe(false);
    expect(isStalled(applying, 0, STALL_MS + 1)).toBe(true);
    expect(isStalled(run({ state: "done" }), 0, STALL_MS + 1)).toBe(false);
  });
  it("shows a closed run again while it writes, and its unseen report once", () => {
    expect(keepOnReopen(run({ state: "applying" }), false)).toBe(true);
    expect(keepOnReopen(run({ state: "done" }), false)).toBe(true);
    expect(keepOnReopen(run({ state: "done" }), true)).toBe(false);
    expect(keepOnReopen(run({ state: "planned" }), false)).toBe(false);
    expect(keepOnReopen(null, false)).toBe(false);
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

describe("discard (I-6)", () => {
  it("reads the route's refusal, and nothing else", () => {
    expect(discardRefusal({ message: "A member edited it.", blocking: [{ id: "t1", title: "T" }, { x: 1 }] })).toEqual({
      message: "A member edited it.",
      blocking: [{ id: "t1", title: "T" }],
    });
    expect(discardRefusal("This import is running.")).toBeNull();
    expect(discardRefusal(null)).toBeNull();
  });
  it("says what it removed, and what stays", () => {
    expect(discardLines({ nodes: 62, spaces: 5, tasks: 2423, comments_elsewhere: 1, updates_kept: 2 })).toEqual([
      "Removed 5 spaces and 2,423 tasks.",
      "Removed 57 folders and lists it created.",
      "Removed 1 comment it added to tasks an earlier import made.",
      "2 updates to tasks an earlier import made stay. The earlier values are not kept.",
    ]);
    expect(discardLines({})).toEqual(["The import had written nothing, so nothing was removed."]);
  });
  it("describes a run in the list by what it wrote", () => {
    const line = runLine({
      id: "r",
      source: "clickup",
      state: "done",
      created_by: "a@x.test",
      created_at: "2026-09-28T10:00:00Z",
      finished_at: "2026-09-28T10:01:00Z",
      summary: null,
      tasks_written: 2423,
      discarded: null,
      discard_until: null,
      discardable: true,
    });
    expect(line.state).toBe("Done");
    expect(line.what).toBe("2,423 new tasks");
    const gone = runLine({
      id: "r",
      source: "clickup",
      state: "discarded",
      created_by: "a@x.test",
      created_at: null,
      finished_at: null,
      summary: null,
      tasks_written: 0,
      discarded: { tasks: 2423 },
      discard_until: null,
      discardable: false,
    });
    expect(gone.what).toBe("Removed 2,423 tasks");
  });
});

describe("the history list", () => {
  it("is mounted in the upload step, so a reload can find a run again", () => {
    const dialog = readFileSync(join(__dirname, "..", "components", "ImportDialog.tsx"), "utf-8");
    expect(dialog).toContain("<ImportHistory");
    expect(dialog).toContain("<DiscardImportButton");
  });
});

describe("an admin can find the import (I-7, then WS-42)", () => {
  // The owner looked for it, and an unlabelled icon beside the + read as "no
  // import UI". A labelled sidebar row followed, and the owner asked for it
  // to live in Projects settings instead (D81). The empty tree had a link
  // too, and the owner removed it on 2026-09-30: Settings is the one entry.
  const page = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");
  it("is offered in settings only, and never under the empty tree", () => {
    expect(page).not.toMatch(/onImport/);
    expect(page).toMatch(/mayImport=\{mayImport\}/);
  });
});
