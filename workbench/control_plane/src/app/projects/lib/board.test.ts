/**
 * Projects · board maths — the arithmetic a drag depends on.
 *
 * Spec: `project-docs/specs/project_management_app.md` §3.10 · D-PM-5 ·
 * ticket WS-27d done-when 2.
 */
import { describe, expect, it } from "vitest";

import {
  POSITION_MAX,
  SAVED_VIEW_POSITION,
  buildCellDropPatch,
  buildColumnDropUpdate,
  currentAxisKey,
  dropRefusal,
  orderBearingView,
  planDrop,
  positionBetween,
  sortForView,
} from "./board";
import { UNSET } from "./grouping";

describe("positionBetween", () => {
  it("halves the gap between two neighbours", () => {
    expect(positionBetween(100, 200)).toBe(150);
  });

  it("appends above the last card without reaching the ceiling", () => {
    const appended = positionBetween(100, null);
    expect(appended).toBeGreaterThan(100);
    expect(appended).toBeLessThan(POSITION_MAX);
  });

  it("prepends below the first card without reaching zero", () => {
    const prepended = positionBetween(null, 100);
    expect(prepended).toBeGreaterThan(0);
    expect(prepended).toBeLessThan(100);
  });

  it("never produces a negative position", () => {
    // The property that makes the bounds structural rather than validated.
    let position = positionBetween(null, 100);
    for (let i = 0; i < 40; i += 1) position = positionBetween(null, position);
    expect(position).toBeGreaterThan(0);
  });

  it("puts a lone card in the middle so both directions stay open", () => {
    expect(positionBetween(null, null)).toBe(POSITION_MAX / 2);
  });
});

describe("sortForView", () => {
  it("orders positioned cards by position", () => {
    const sorted = sortForView([
      { id: "b", view_position: 200 },
      { id: "a", view_position: 100 },
    ]);
    expect(sorted.map((t) => t.id)).toEqual(["a", "b"]);
  });

  it("sorts unpositioned cards to the BOTTOM, by age", () => {
    // Not the top: a new task appearing above a hand-arranged column would
    // look like the board reordered itself.
    const sorted = sortForView([
      { id: "new", created_at: "2026-08-06T10:00:00Z" },
      { id: "older", created_at: "2026-08-01T10:00:00Z" },
      { id: "placed", view_position: 500 },
    ]);
    expect(sorted.map((t) => t.id)).toEqual(["placed", "older", "new"]);
  });
});

describe("planDrop", () => {
  const ordered = [
    { id: "a", view_position: 100 },
    { id: "b", view_position: 200 },
    { id: "c", view_position: 300 },
  ];

  it("writes exactly ONE row for a normal drop", () => {
    const writes = planDrop(ordered, "c", 1);
    expect(writes).toHaveLength(1);
    expect(writes[0].task_id).toBe("c");
    expect(writes[0].position).toBe(150);
  });

  it("carries the destination group so a cross-column drag is still one write", () => {
    const writes = planDrop(ordered, "c", 0, "status-2");
    expect(writes).toHaveLength(1);
    expect(writes[0].group_key).toBe("status-2");
  });

  it("materialises the whole group on the first drag into an unordered one", () => {
    // As N requests this would leave the order half-applied if one failed —
    // which is why the bulk endpoint is the preferred shape, not a convenience.
    const unordered = [{ id: "x" }, { id: "y" }, { id: "z" }];
    const writes = planDrop(unordered, "z", 0);

    expect(writes).toHaveLength(3);
    expect(writes.map((w) => w.task_id)).toEqual(["z", "x", "y"]);
    const positions = writes.map((w) => w.position);
    expect([...positions].sort((p, q) => p - q)).toEqual(positions);
    expect(positions.every((p) => p > 0 && p < POSITION_MAX)).toBe(true);
  });

  it("clamps an out-of-range index instead of producing NaN", () => {
    expect(planDrop(ordered, "a", 99)[0].position).toBeGreaterThan(300);
    expect(planDrop(ordered, "c", -5)[0].position).toBeLessThan(100);
  });
});

describe("buildColumnDropUpdate", () => {
  it("patches whatever field the view groups by", () => {
    // Hard-coding status is what makes a board that can only group by status.
    expect(buildColumnDropUpdate("status", "s-1")).toEqual({ status_id: "s-1" });
    expect(buildColumnDropUpdate("type", "t-1")).toEqual({ type_id: "t-1" });
    expect(buildColumnDropUpdate("project", "p-1")).toEqual({ project_id: "p-1" });
  });

  it("patches nothing for an unknown grouping, and does not throw", () => {
    // The drop still reorders within the column; it simply moves no field.
    expect(buildColumnDropUpdate("assignee", "someone")).toBeNull();
    expect(buildColumnDropUpdate(undefined, null)).toBeNull();
  });

  it("patches importance as an integer, and the UNSET lane as null (WS-27y)", () => {
    // The gateway's TaskIn declares `importance: int`; "2" would 422, and
    // Number(UNSET) would be NaN.
    expect(buildColumnDropUpdate("importance", "2")).toEqual({ importance: 2 });
    expect(buildColumnDropUpdate("importance", "0")).toEqual({ importance: 0 });
    expect(buildColumnDropUpdate("importance", UNSET)).toEqual({ importance: null });
  });
});

describe("dropRefusal (WS-27y)", () => {
  it("allows the single-valued plain-PATCH axes", () => {
    expect(dropRefusal("status")).toBeNull();
    expect(dropRefusal("importance")).toBeNull();
    expect(dropRefusal("none")).toBeNull();
    expect(dropRefusal(undefined)).toBeNull();
  });

  it("refuses many-valued axes, and says why", () => {
    expect(dropRefusal("assignee")).toMatch(/many-valued/i);
    expect(dropRefusal("tag")).toMatch(/many-valued/i);
  });

  it("refuses a project move with the grant boundary named", () => {
    expect(dropRefusal("project")).toMatch(/grant boundary/i);
  });

  it("refuses a lane cell when EITHER axis is unwritable", () => {
    // Writing half of what the cell means would file the card somewhere it
    // is not.
    expect(dropRefusal("status", "assignee")).toMatch(/many-valued/i);
    expect(dropRefusal("assignee", "status")).toMatch(/many-valued/i);
    expect(dropRefusal("status", "importance")).toBeNull();
  });

  it("refuses an axis it has never heard of, naming it", () => {
    expect(dropRefusal("phase")).toMatch(/phase/);
  });

  it("refuses to move an archived task at all", () => {
    expect(dropRefusal("status", null, { id: "t", archived_at: "2026-08-01" })).toMatch(
      /archived/i
    );
  });
});

describe("currentAxisKey", () => {
  const task = {
    status_id: "s-1",
    project_id: "p-1",
    importance: 0,
  };

  it("reads the bucket a task sits in per axis", () => {
    expect(currentAxisKey(task, "status")).toBe("s-1");
    expect(currentAxisKey(task, "project")).toBe("p-1");
  });

  it("keeps importance 0 a real bucket and missing importance UNSET", () => {
    expect(currentAxisKey(task, "importance")).toBe("0");
    expect(currentAxisKey({ importance: null }, "importance")).toBe(UNSET);
    expect(currentAxisKey({}, "importance")).toBe(UNSET);
  });

  it("has no single answer for many-valued or unknown axes", () => {
    expect(currentAxisKey(task, "assignee")).toBeNull();
    expect(currentAxisKey(task, undefined)).toBeNull();
  });
});

describe("buildCellDropPatch (WS-27y)", () => {
  const task = { status_id: "s-todo", importance: null };

  it("sets BOTH axes when a card lands in a foreign cell", () => {
    expect(
      buildCellDropPatch(task, "status", "s-doing", "importance", "2")
    ).toEqual({ status_id: "s-doing", importance: 2 });
  });

  it("patches only the axis that actually moved", () => {
    // Dropped elsewhere in its own column: the lane changed, status did not —
    // patching status anyway would write an activity row about a non-move.
    expect(buildCellDropPatch(task, "status", "s-todo", "importance", "1")).toEqual({
      importance: 1,
    });
  });

  it("patches nothing for a drop into the cell it came from", () => {
    expect(buildCellDropPatch(task, "status", "s-todo", "importance", UNSET)).toBeNull();
  });

  it("works without a lane axis — the flat board's column drop", () => {
    expect(buildCellDropPatch(task, "status", "s-doing")).toEqual({
      status_id: "s-doing",
    });
    expect(buildCellDropPatch(task, "status", "s-todo")).toBeNull();
  });

  it("clears priority when dropped into the no-priority lane", () => {
    expect(
      buildCellDropPatch({ status_id: "s-todo", importance: 2 }, "status", "s-todo", "importance", UNSET)
    ).toEqual({ importance: null });
  });
});

describe("orderBearingView", () => {
  /**
   * The views a project is seeded with, in the order the server returns them
   * (`ORDER BY position NULLS LAST, name` — see `views.list_views`).
   */
  const seeded = [
    { id: "v-list", name: "All tasks", view_type: "list", position: 100 },
    { id: "v-board", name: "Board", view_type: "board", position: 200 },
  ];

  it("is the project's seeded board", () => {
    expect(orderBearingView(seeded)?.id).toBe("v-board");
  });

  it("ignores list views however they are ordered", () => {
    expect(orderBearingView([...seeded].reverse())?.id).toBe("v-board");
  });

  it("is not stolen by a board view somebody saved later", () => {
    // The one that matters: every drag writes its positions here, so if a
    // saved filter could become it, arranging the board would silently
    // reorder a *filtered* subset and the real board would look shuffled.
    const withSaved = [
      ...seeded,
      { id: "v-mine", name: "Mine", view_type: "board", position: SAVED_VIEW_POSITION },
    ];
    expect(orderBearingView(withSaved)?.id).toBe("v-board");
  });

  it("saves above the seeded pair, which is what keeps that true", () => {
    // The claim above is only true because the server orders by position. If
    // a saved view were allowed below 200 it would sort first and win.
    for (const view of seeded) {
      expect(SAVED_VIEW_POSITION).toBeGreaterThan(view.position);
    }
  });

  it("is null for a project with no board at all, rather than throwing", () => {
    expect(orderBearingView([])).toBeNull();
    expect(orderBearingView([seeded[0]])).toBeNull();
  });
});

describe("dropping on a STAGE column (§9.12.3)", () => {
  const lane = (id: string, name: string, category: string, position: number) => ({
    id,
    name,
    category,
    position,
  });
  // Three in-progress lanes, so "which one" has a real answer to get wrong.
  const LANES = [
    lane("l-back", "Backlog", "backlog", 0),
    lane("l-prog", "In progress", "in_progress", 1),
    lane("l-block", "Blocked", "in_progress", 2),
    lane("l-review", "In review", "in_progress", 3),
    lane("l-done", "Done", "done", 4),
  ];

  it("lands on the FIRST lane of the stage, by position", () => {
    expect(buildColumnDropUpdate("category", "in_progress", LANES)).toEqual({
      status_id: "l-prog",
    });
  });

  it("does NOT use is_default, which the owner retired", () => {
    // ⚠️ The spec still says "the project's DEFAULT status in that category".
    // Owner directive 2026-09-06 retired that flag because it sat on `backlog`
    // for every space, leaving three of four stages with no answer. Position
    // order always has one. A lane marked default and sitting second must lose.
    const flagged = [
      { ...lane("l-prog", "In progress", "in_progress", 1) },
      { ...lane("l-block", "Blocked", "in_progress", 2), is_default: true },
    ];
    expect(buildColumnDropUpdate("category", "in_progress", flagged)).toEqual({
      status_id: "l-prog",
    });
  });

  it("patches NOTHING for a stage with no lane, rather than inventing one", () => {
    expect(buildColumnDropUpdate("category", "cancelled", LANES)).toBeNull();
  });

  it("patches nothing when no lanes were handed to it", () => {
    // Every pre-existing caller passes two arguments. Silently choosing a lane
    // from an empty list would be worse than doing nothing.
    expect(buildColumnDropUpdate("category", "in_progress")).toBeNull();
  });

  it("a drop INSIDE its own stage writes no status at all", () => {
    // ⚠️ The one that protects real work. Without a stage-aware
    // `currentAxisKey`, dragging a "Blocked" card one inch inside the
    // In-progress column patches it onto that stage's FIRST lane — silently
    // demoting it to "In progress". A reorder must stay a reorder.
    const blocked = { id: "t1", status_id: "l-block" };
    expect(
      buildCellDropPatch(blocked, "category", "in_progress", null, null, LANES)
    ).toBeNull();
  });

  it("a drop into a DIFFERENT stage does write", () => {
    const blocked = { id: "t1", status_id: "l-block" };
    expect(
      buildCellDropPatch(blocked, "category", "done", null, null, LANES)
    ).toEqual({ status_id: "l-done" });
  });

  it("is not refused — a stage is a writable axis", () => {
    expect(dropRefusal("category")).toBeNull();
    expect(dropRefusal("status", "category")).toBeNull();
  });
});
