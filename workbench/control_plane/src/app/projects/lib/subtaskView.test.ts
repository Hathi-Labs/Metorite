/**
 * D-PM-38 (Subtasks S3) — how each Projects canvas draws a subtask.
 *
 * The rules, as assertions: which modes a canvas offers and its default, what
 * the member's overlay does, the rows the list and the table draw, the group
 * count that does not move when a parent collapses (B7), the filter's
 * auto-open, and the board column's tooltip. The rendered checks (the list
 * and the table agree, the board draws the crumb) are in
 * `components/subtaskViews.test.ts`.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { TaskRow } from "./api";
import {
  collapsedNow,
  columnCountTitle,
  defaultSubtaskMode,
  drawnIds,
  effectiveSubtaskMode,
  overlayWithSubtasks,
  selectAllDrawn,
  sendsTopLevel,
  storedSubtasks,
  subtaskModesFor,
  subtaskRows,
  subtaskSections,
  toggleFold,
  visibleTasks,
} from "./subtaskView";

const task = (id: string, parent: string | null = null): TaskRow => ({
  id,
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: id,
  parent_task_id: parent,
  parent: parent ? { id: parent, ref: null, title: parent } : null,
});

/** A parent, two children and a grandchild under the first child. */
const FAMILY = [
  task("parent"),
  task("child-1", "parent"),
  task("grandchild", "child-1"),
  task("child-2", "parent"),
  task("solo"),
];

describe("which modes each canvas offers, and its default (§12.9)", () => {
  it("the list and the table offer all three and default to Nested", () => {
    for (const mode of ["list", "table"] as const) {
      expect(subtaskModesFor(mode)).toEqual(["nested", "separate", "hidden"]);
      expect(defaultSubtaskMode(mode)).toBe("nested");
    }
  });

  it("the board defaults to Separate (owner decision 1) and offers no Nested", () => {
    expect(defaultSubtaskMode("board")).toBe("separate");
    expect(subtaskModesFor("board")).toEqual(["separate", "hidden"]);
  });

  it("the calendar offers only Separate and Hidden, and defaults to Separate", () => {
    expect(subtaskModesFor("calendar")).toEqual(["separate", "hidden"]);
    expect(defaultSubtaskMode("calendar")).toBe("separate");
  });

  it("the timeline is Nested (D-PM-11) and shows no control", () => {
    expect(subtaskModesFor("timeline")).toEqual([]);
    for (const stored of ["nested", "separate", "hidden", null] as const) {
      expect(effectiveSubtaskMode(stored, "timeline")).toBe("nested");
      expect(sendsTopLevel(stored, "timeline")).toBe(false);
    }
  });

  it("a stored mode a canvas does not offer falls back to its default", () => {
    expect(effectiveSubtaskMode("nested", "board")).toBe("separate");
    expect(effectiveSubtaskMode("nested", "calendar")).toBe("separate");
    expect(effectiveSubtaskMode("nested", "list")).toBe("nested");
    expect(effectiveSubtaskMode(null, "list")).toBe("nested");
  });

  it("only Hidden asks the server for top-level tasks", () => {
    expect(sendsTopLevel("hidden", "list")).toBe(true);
    expect(sendsTopLevel("hidden", "board")).toBe(true);
    expect(sendsTopLevel("hidden", "calendar")).toBe(true);
    expect(sendsTopLevel("separate", "board")).toBe(false);
    expect(sendsTopLevel(null, "board")).toBe(false);
  });
});

describe("the member's overlay holds the mode", () => {
  it("the member's overlay wins over the view's stored mode", () => {
    expect(
      storedSubtasks({ config: { subtasks: "nested" }, user_state: { subtasks: "hidden" } }),
    ).toBe("hidden");
    expect(storedSubtasks({ config: { subtasks: "nested" } })).toBe("nested");
    expect(storedSubtasks({ config: {}, user_state: {} })).toBeNull();
    expect(storedSubtasks(null)).toBeNull();
  });

  it("a junk value in either place reads as no mode", () => {
    expect(storedSubtasks({ config: { subtasks: "flat" }, user_state: { subtasks: 3 } })).toBeNull();
  });

  it("the PUT body keeps every other overlay key", () => {
    // The overlay endpoint REPLACES the overlay. A body of `{subtasks}`
    // alone would wipe the member's collapsed lanes.
    expect(
      overlayWithSubtasks({ collapsed_lanes: ["a"], subtasks: "nested" }, "hidden"),
    ).toEqual({ collapsed_lanes: ["a"], subtasks: "hidden" });
    expect(overlayWithSubtasks(undefined, "separate")).toEqual({ subtasks: "separate" });
  });
});

describe("the rows the list and the table draw", () => {
  it("Nested puts every level under its parent", () => {
    const rows = subtaskRows(FAMILY, "nested");
    expect(rows.map((r) => [r.task.id, r.depth, r.crumb])).toEqual([
      ["parent", 0, false],
      ["child-1", 1, false],
      ["grandchild", 2, false],
      ["child-2", 1, false],
      ["solo", 0, false],
    ]);
  });

  it("Nested shows an orphan alone, with its crumb (owner decision 3)", () => {
    // A filter matched the child and not its parent. No greyed context row.
    const rows = subtaskRows([task("child-1", "parent"), task("solo")], "nested");
    expect(rows.map((r) => [r.task.id, r.depth, r.crumb])).toEqual([
      ["child-1", 0, true],
      ["solo", 0, false],
    ]);
  });

  it("Separate draws flat rows, and every subtask carries its crumb", () => {
    const rows = subtaskRows(FAMILY, "separate");
    expect(rows.map((r) => [r.task.id, r.depth, r.crumb])).toEqual([
      ["parent", 0, false],
      ["child-1", 0, true],
      ["grandchild", 0, true],
      ["child-2", 0, true],
      ["solo", 0, false],
    ]);
  });

  it("Hidden draws top-level rows only", () => {
    expect(subtaskRows(FAMILY, "hidden").map((r) => r.task.id)).toEqual(["parent", "solo"]);
    expect(visibleTasks(FAMILY, "hidden").map((t) => t.id)).toEqual(["parent", "solo"]);
    expect(visibleTasks(FAMILY, "separate")).toHaveLength(FAMILY.length);
  });
});

describe("the group count does not depend on collapse (B7)", () => {
  const groups = [{ key: "todo", label: "To do", tasks: FAMILY }];

  it("a collapsed parent hides rows and keeps the count", () => {
    const open = subtaskSections(groups, "nested", new Set());
    const shut = subtaskSections(groups, "nested", new Set(["parent"]));
    expect(open[0].rows).toHaveLength(5);
    expect(shut[0].rows.map((r) => r.task.id)).toEqual(["parent", "solo"]);
    expect(shut[0].count).toBe(5);
    expect(open[0].count).toBe(5);
  });

  it("Hidden counts the tasks it shows, not the ones it hides", () => {
    expect(subtaskSections(groups, "hidden", new Set())[0].count).toBe(2);
  });

  it("drops a group with nothing to show", () => {
    const onlySubs = [{ key: "g", label: "G", tasks: [task("c", "p")] }];
    expect(subtaskSections(onlySubs, "hidden", new Set())).toEqual([]);
  });
});

describe("a new filter opens every parent", () => {
  it("opens the member's collapsed parents when a filter is newly active", () => {
    const fold = { key: JSON.stringify({}), ids: new Set(["parent"]) };
    const filterKey = JSON.stringify({ q: "grand" });
    expect(collapsedNow(fold, filterKey, true).size).toBe(0);
  });

  it("keeps the member's collapse when nothing is filtering", () => {
    const fold = { key: "a", ids: new Set(["parent"]) };
    expect([...collapsedNow(fold, "{}", false)]).toEqual(["parent"]);
  });

  it("a collapse made under the filter holds until the filter changes", () => {
    const filterKey = JSON.stringify({ q: "grand" });
    const after = toggleFold({ key: "{}", ids: new Set() }, filterKey, true, "parent");
    expect([...collapsedNow(after, filterKey, true)]).toEqual(["parent"]);
    expect(collapsedNow(after, JSON.stringify({ q: "other" }), true).size).toBe(0);
  });
});

describe("the board column's count tooltip", () => {
  it("says how many of the cards are subtasks", () => {
    expect(columnCountTitle(FAMILY, "To do")).toBe("5 tasks (3 subtasks) in To do");
    expect(columnCountTitle([task("c", "p")], "Done")).toBe("1 task (1 subtask) in Done");
  });

  it("leaves the parenthesis out when there are none", () => {
    expect(columnCountTitle([task("a"), task("b")], "Done")).toBe("2 tasks in Done");
  });
});

// ── Review of PR #491: select-all takes only what is drawn ──────────────────

describe("select-all takes only the drawn rows", () => {
  const groups = [{ key: "todo", label: "To do", tasks: FAMILY }];

  it("a collapsed parent's subtasks are not selected", () => {
    // Fold `parent` (3 tasks under it), then select all: 2 rows, not 5.
    const sections = subtaskSections(groups, "nested", new Set(["parent"]));
    const drawn = drawnIds(sections);
    expect(drawn).toEqual(["parent", "solo"]);
    expect([...selectAllDrawn(drawn, new Set())]).toEqual(["parent", "solo"]);
  });

  it("a folded group section draws nothing, so it selects nothing", () => {
    const sections = subtaskSections(groups, "nested", new Set());
    expect(drawnIds(sections, new Set(["todo"]))).toEqual([]);
  });

  it("with every drawn row selected, the box clears the selection", () => {
    expect(selectAllDrawn(["a", "b"], new Set(["a", "b"])).size).toBe(0);
    expect([...selectAllDrawn(["a", "b"], new Set(["a"]))]).toEqual(["a", "b"]);
  });

  it("the list reads the drawn rows, not the whole groups", () => {
    // The wiring half: the page stores what the list hands it, and the list
    // builds it from `rows`, which is `drawnIds`.
    const list = readFileSync(
      fileURLToPath(new URL("../components/TaskList.tsx", import.meta.url)),
      "utf8",
    );
    expect(list).toMatch(/const rows = useMemo\(\(\) => drawnIds\(sections, folded\)/);
    expect(list).toMatch(/onToggleAll\?\.\(selectAllDrawn\(rows,/);
    const page = readFileSync(fileURLToPath(new URL("../page.tsx", import.meta.url)), "utf8");
    expect(page).toMatch(/onToggleAll=\{setPicked\}/);
  });
});
