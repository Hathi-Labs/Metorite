/**
 * WS-27x — the spreadsheet layout's row and column model.
 *
 * The three claims that decide whether the table is trustworthy: the columns
 * a shown-field set produces (and their canonical order), the sub-task
 * indentation model over `parent_task_id` (a filtered-out parent must not
 * swallow its children), and the header-sort mapping onto the sort keys the
 * gateway actually accepts — an unknown key there is a 422, not a fallback.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { FieldDef } from "./customFields";
import { DEFAULT_SHOWN, FIELD_KEYS } from "./shownFields";
import {
  IMPORTANCE_OPTIONS,
  TASK_SORT_KEYS,
  customKeyOf,
  importanceLabel,
  nextSort,
  sortQuery,
  LIST_GATED_COLUMNS,
  listColumns,
  tableColumns,
  treeRows,
} from "./table";

const def = (field_key: string, over: Partial<FieldDef> = {}): FieldDef => ({
  id: `f-${field_key}`,
  project_id: "p1",
  field_key,
  name: field_key,
  field_type: "text",
  options: [],
  position: 0,
  ...over,
});

describe("the sort mapping mirrors the gateway", () => {
  it("matches core.py TASK_SORTS key for key", () => {
    // Read from the gateway source, not restated: a key added or removed
    // there without this mirror moving is a header click that answers 422.
    const source = readFileSync(
      resolve(
        __dirname,
        "../../../../../../apps/services/gateway/gateway/routes/projects/core.py"
      ),
      "utf-8"
    );
    const block = source.match(/TASK_SORTS: dict\[str, str\] = \{([\s\S]*?)\n\}/);
    expect(block, "core.py no longer declares TASK_SORTS").toBeTruthy();
    const serverKeys = [...block![1].matchAll(/^\s*"([a-z_]+)":/gm)].map(
      (m) => m[1]
    );
    expect([...serverKeys].sort()).toEqual([...TASK_SORT_KEYS].sort());
  });

  it("only ever emits sort keys the server accepts", () => {
    const shownEverything = [...FIELD_KEYS];
    for (const column of tableColumns(shownEverything, [])) {
      if (column.sortKey !== null) {
        expect(TASK_SORT_KEYS).toContain(column.sortKey);
      }
    }
  });

  it("gives a custom column no sort — there is no server ordering for it", () => {
    const columns = tableColumns(["custom.budget"], [def("budget")]);
    expect(columns).toHaveLength(1);
    expect(columns[0].sortKey).toBeNull();
  });
});

describe("nextSort — one header, three states", () => {
  it("cycles unsorted → asc → desc → back to the view's own order", () => {
    const asc = nextSort(null, "due_at");
    expect(asc).toEqual({ key: "due_at", dir: "asc" });
    const desc = nextSort(asc, "due_at");
    expect(desc).toEqual({ key: "due_at", dir: "desc" });
    // The third click matters: without it the table can never return to the
    // board's hand-arranged `sortForView` order.
    expect(nextSort(desc, "due_at")).toBeNull();
  });

  it("starts a different column ascending, whatever the current state", () => {
    expect(nextSort({ key: "due_at", dir: "desc" }, "status")).toEqual({
      key: "status",
      dir: "asc",
    });
  });

  it("ignores a click on an unsortable header", () => {
    const current = { key: "due_at", dir: "asc" } as const;
    expect(nextSort(current, null)).toBe(current);
  });
});

describe("sortQuery", () => {
  it("emits the existing wire parameters, and nothing when unsorted", () => {
    expect(sortQuery({ key: "status", dir: "desc" })).toEqual({
      sort: "status",
      direction: "desc",
    });
    expect(sortQuery(null)).toEqual({});
  });
});

describe("tableColumns", () => {
  it("draws the default set in the vocabulary's order", () => {
    expect(tableColumns(DEFAULT_SHOWN, []).map((c) => c.key)).toEqual([
      "status",
      // ⚠️ No `type` and no `source`. Both chips exist, and neither key is in
      // DEFAULT_SHOWN — nothing writes `pm_tasks.type_id`, so the columns
      // would read "—" on every row. See `shownFields.ts` for the argument.
      "assignees",
      "due_at",
      "importance",
      "subtasks",
      "blocked",
      "tags",
    ]);
  });

  it("ignores the stored list's order — shown_fields is a set", () => {
    // A hand-shuffled config must not move the Status column.
    const shuffled = [...DEFAULT_SHOWN].reverse();
    expect(tableColumns(shuffled, [])).toEqual(tableColumns(DEFAULT_SHOWN, []));
  });

  it("appends custom columns after the core ones, in registry order", () => {
    const defs = [def("b", { position: 2, name: "B" }), def("a", { position: 1, name: "A" })];
    const columns = tableColumns(["status", "custom.b", "custom.a"], defs);
    expect(columns.map((c) => c.key)).toEqual(["status", "custom.a", "custom.b"]);
    expect(columns[1].label).toBe("A");
    expect(columns[1].def).toBe(defs[1]);
  });

  it("draws no column for a shown custom field whose definition was deleted", () => {
    // The view outlived the field. A header with no data and no editor under
    // it is a column of nothing.
    expect(tableColumns(["status", "custom.gone"], [])).toHaveLength(1);
  });

  it("draws nothing at all for an emptied set", () => {
    expect(tableColumns([], [def("a")])).toEqual([]);
  });
});

describe("customKeyOf", () => {
  it("unwraps the prefix and refuses everything else", () => {
    expect(customKeyOf("custom.budget")).toBe("budget");
    expect(customKeyOf("status")).toBeNull();
  });
});

describe("importance vocabulary", () => {
  it("treats 0 as Low, never as unset — it is falsy", () => {
    expect(importanceLabel(0)).toBe("Low");
    expect(importanceLabel(null)).toBe("");
    expect(importanceLabel(3)).toBe("Urgent");
  });

  it("offers an explicit unset row so the select can be emptied", () => {
    expect(IMPORTANCE_OPTIONS[0]).toEqual({ value: "", label: "No priority" });
  });
});

// ── the indentation model ───────────────────────────────────────────────────

const t = (id: string, parent?: string | null) => ({
  id,
  parent_task_id: parent ?? null,
});

const NONE: ReadonlySet<string> = new Set();

describe("treeRows — sub-tasks indent under an on-page parent", () => {
  it("nests children directly under their parent, depth-first", () => {
    const rows = treeRows([t("a"), t("b"), t("a1", "a"), t("a1x", "a1")], NONE);
    expect(rows.map((r) => [r.task.id, r.depth])).toEqual([
      ["a", 0],
      ["a1", 1],
      ["a1x", 2],
      ["b", 0],
    ]);
  });

  it("counts direct children so the caret knows when to draw", () => {
    const rows = treeRows([t("a"), t("a1", "a"), t("a2", "a")], NONE);
    expect(rows[0].childCount).toBe(2);
    expect(rows[1].childCount).toBe(0);
  });

  it("keeps the incoming order within each level — the caller sorted it", () => {
    const rows = treeRows([t("b"), t("a"), t("b2", "b"), t("b1", "b")], NONE);
    expect(rows.map((r) => r.task.id)).toEqual(["b", "b2", "b1", "a"]);
  });

  it("surfaces a sub-task whose parent is not on the page, flat", () => {
    // The filter said "show this task"; hiding it because its parent did not
    // qualify would make a filtered table lose rows silently.
    const rows = treeRows([t("orphan", "elsewhere"), t("a")], NONE);
    expect(rows.map((r) => [r.task.id, r.depth])).toEqual([
      ["orphan", 0],
      ["a", 0],
    ]);
  });

  it("hides a collapsed parent's whole subtree, grandchildren included", () => {
    const rows = treeRows(
      [t("a"), t("a1", "a"), t("a1x", "a1"), t("b")],
      new Set(["a"])
    );
    expect(rows.map((r) => r.task.id)).toEqual(["a", "b"]);
  });

  it("collapsing a mid-level node keeps its siblings", () => {
    const rows = treeRows(
      [t("a"), t("a1", "a"), t("a2", "a"), t("a1x", "a1")],
      new Set(["a1"])
    );
    expect(rows.map((r) => r.task.id)).toEqual(["a", "a1", "a2"]);
  });

  it("loses no rows to a parent cycle, and does not hang", () => {
    // Impossible server-side (`assert_no_task_cycle`); a stale page must
    // degrade to missing indentation, never to a hung tab or vanished work.
    const rows = treeRows([t("x", "y"), t("y", "x")], NONE);
    expect(rows.map((r) => r.task.id).sort()).toEqual(["x", "y"]);
  });

  it("treats a self-parented row as a root rather than recursing", () => {
    expect(treeRows([t("a", "a")], NONE).map((r) => r.depth)).toEqual([0]);
  });
});

describe("the list's columns (WS-27ab item 6)", () => {
  it("draws the default view exactly as it drew it before the gate existed", () => {
    // The whole point of the default: turning the gate on hides nothing from
    // anybody who never touched the field picker.
    expect(listColumns(DEFAULT_SHOWN, true)).toEqual([
      "select",
      "ref",
      "title",
      "status",
      "assignees",
      "details",
    ]);
    expect(listColumns(DEFAULT_SHOWN, false)).toEqual([
      "ref",
      "title",
      "status",
      "assignees",
      "details",
    ]);
  });

  it("drops the column a view hid, and only that one", () => {
    expect(listColumns(["assignees", "due_at"], false)).toEqual([
      "ref",
      "title",
      "assignees",
      "details",
    ]);
    expect(listColumns(["status"], false)).toEqual([
      "ref",
      "title",
      "status",
      "details",
    ]);
  });

  it("keeps the three fixed columns even when every field is hidden", () => {
    // `#`, the title and the chip strip are the row itself, not fields — a
    // list with no title column is not a list.
    expect(listColumns([], false)).toEqual(["ref", "title", "details"]);
  });

  it("gates exactly the two the list can gate, both of them defaults", () => {
    for (const key of LIST_GATED_COLUMNS) {
      expect(FIELD_KEYS as readonly string[]).toContain(key);
      expect(DEFAULT_SHOWN).toContain(key);
    }
  });

  it("ignores a key the list has no column for", () => {
    expect(listColumns(["tags", "custom.owner"], false)).toEqual([
      "ref",
      "title",
      "details",
    ]);
  });

  it("gives the header, the group heading and the quick-add one number", () => {
    // The colSpan bug this exists to prevent: two hand-counted numbers.
    expect(listColumns(DEFAULT_SHOWN, true)).toHaveLength(6);
    expect(listColumns(["status"], true)).toHaveLength(5);
  });
});
