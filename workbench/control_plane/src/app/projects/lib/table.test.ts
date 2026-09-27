/**
 * WS-27x — the spreadsheet layout's row and column model.
 *
 * The claims that decide whether the table is trustworthy: the columns
 * a shown-field set produces (and their canonical order), and the header-sort
 * mapping onto the sort keys the
 * gateway actually accepts — an unknown key there is a 422, not a fallback.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { FieldDef } from "./customFields";
import { DEFAULT_SHOWN, FIELD_KEYS } from "./shownFields";
import { MATRIX_FLAG_OPTIONS, flagsOf, flagsPatch } from "./matrix";
import {
  TASK_SORT_KEYS,
  customKeyOf,
  nextSort,
  sortQuery,
  LIST_GATED_COLUMNS,
  listColumns,
  tableColumns,
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

describe("the priority cell's editor (D78)", () => {
  it("offers a 'Not flagged' row so the flags can be cleared", () => {
    expect(MATRIX_FLAG_OPTIONS[0]).toEqual({ value: "", label: "Not flagged" });
    expect(flagsPatch("")).toEqual({ importance: 0, leveraged: false });
  });

  it("shows a row's current flags as the selected option", () => {
    const values = MATRIX_FLAG_OPTIONS.map((o) => o.value);
    expect(values).toContain(flagsOf({ importance: 2, leveraged: true }));
    expect(values).toContain(flagsOf({ importance: null, leveraged: null }));
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
