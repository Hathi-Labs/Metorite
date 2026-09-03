import { describe, expect, it } from "vitest";

import { EDITABLE_CATEGORIES, STATUS_CATEGORIES } from "@/lib/statusCategory";

import {
  POSITION_STEP,
  type Placeable,
  emptyCategories,
  isLastInCategory,
  nextPosition,
  placeNew,
  reorder,
  siblings,
} from "./statusOrder";

/** Every stage the editor can file a lane under. */
const EVERY_CATEGORY = EDITABLE_CATEGORIES;

/**
 * The fence for `statusOrder.ts` (R7).
 *
 * The seeded shape every case below starts from, measured on the dev database
 * 2026-09-03: four statuses per root, one per category, spaced by ten.
 */
const SEED: Placeable[] = [
  { id: "s-backlog", name: "Backlog", category: "backlog", position: 10 },
  { id: "s-todo", name: "To do", category: "todo", position: 20 },
  { id: "s-doing", name: "In progress", category: "in_progress", position: 30 },
  { id: "s-done", name: "Done", category: "done", position: 40 },
];

describe("nextPosition", () => {
  it("puts a new status after the last one in its own category", () => {
    // "In review" joins in_progress (30) and must stay ahead of Done (40), so
    // it takes the midpoint. `+ STEP` returned 40 here and TIED with Done —
    // see the collision test below for what that did to the board.
    expect(nextPosition(SEED, "in_progress")).toBe(35);
  });

  it("never writes a position another row already holds", () => {
    // The measured regression. `+ STEP` gave the new in_progress lane 40, the
    // position Done held. The editor still looked right because it groups by
    // category; the BOARD orders by `position, name` and drew the in-progress
    // lane to the RIGHT of the done lane. A tie is resolved by spelling, which
    // no later reorder can repair.
    for (const category of EVERY_CATEGORY) {
      const { position, renumber } = placeNew(SEED, category);
      const after = SEED.map((r) => {
        const moved = renumber.find((p) => p.id === r.id);
        return moved ? { ...r, position: moved.position } : r;
      });
      const taken = after.map((r) => r.position);
      expect(
        taken,
        `placing a ${category} lane at ${position} collides with an existing row`
      ).not.toContain(position);
    }
  });

  it("keeps the board in lifecycle order after the insert", () => {
    // The property that actually matters: read the board the way the board
    // reads it — by position — and the stages must not interleave.
    for (const category of EVERY_CATEGORY) {
      const { position, renumber } = placeNew(SEED, category);
      const after = [
        ...SEED.map((r) => {
          const moved = renumber.find((p) => p.id === r.id);
          return moved ? { ...r, position: moved.position } : r;
        }),
        { id: "new", name: "New lane", category, position },
      ].sort((a, b) => a.position - b.position);

      const ranks = after.map((r) =>
        (STATUS_CATEGORIES as readonly string[]).indexOf(r.category)
      );
      const sorted = [...ranks].sort((a, b) => a - b);
      expect(
        ranks,
        `inserting a ${category} lane interleaved the stages: ${after
          .map((r) => `${r.name}@${r.position}`)
          .join(" ")}`
      ).toEqual(sorted);
    }
  });

  it("renumbers when there is no gap to insert into", () => {
    // Adjacent positions leave nowhere to go, so the board is re-spaced rather
    // than a tie being written.
    const tight: Placeable[] = [
      { id: "a", name: "In progress", category: "in_progress", position: 1 },
      { id: "b", name: "Done", category: "done", position: 2 },
    ];
    const { position, renumber } = placeNew(tight, "in_progress");
    expect(renumber.length).toBeGreaterThan(0);
    const moved = new Map(renumber.map((p) => [p.id, p.position]));
    expect(position).toBeGreaterThan(moved.get("a") ?? 1);
    expect(position).toBeLessThan(moved.get("b") ?? 2);
  });

  it("puts the first status of an empty category after the categories before it", () => {
    // The regression this exists for: `create_status` defaults position to 0,
    // so a first `cancelled` status would have landed at the HEAD of the board,
    // left of Backlog, instead of after Done.
    expect(nextPosition(SEED, "cancelled")).toBe(50);
  });

  it("does not put an empty middle category after the whole board", () => {
    const noTodo = SEED.filter((r) => r.category !== "todo");
    // todo follows backlog (10), so 20 — not after Done.
    expect(nextPosition(noTodo, "todo")).toBe(20);
  });

  it("starts at the step, not at zero, for the very first status", () => {
    // Leaving room to insert something before it later without a renumber.
    expect(nextPosition([], "backlog")).toBe(POSITION_STEP);
  });

  it("goes before the earliest row when nothing precedes the category", () => {
    const onlyDone: Placeable[] = [
      { id: "d", name: "Done", category: "done", position: 10 },
    ];
    // backlog precedes done, and done already holds the lowest position, so
    // backlog has to go STRICTLY below it. A tie is broken by name, which
    // would sort "Backlog" after "Done" — `position` is a sort key, not a
    // rank, so 0 and negatives are fine and a clamp at the step is not.
    expect(nextPosition(onlyDone, "backlog")).toBeLessThan(10);
  });

  it("sorts an unknown category last rather than first", () => {
    // A value a later migration adds must not reorder an existing board on
    // deploy by claiming the front of it.
    expect(nextPosition(SEED, "on_hold")).toBe(50);
  });
});

describe("siblings", () => {
  it("returns one category, ordered by position then name", () => {
    const rows: Placeable[] = [
      ...SEED,
      { id: "b", name: "Zebra", category: "in_progress", position: 30 },
      { id: "c", name: "Apple", category: "in_progress", position: 30 },
    ];
    expect(siblings(rows, "in_progress").map((r) => r.name)).toEqual([
      "Apple",
      "In progress",
      "Zebra",
    ]);
  });

  it("is empty for a category with nothing in it", () => {
    expect(siblings(SEED, "cancelled")).toEqual([]);
  });
});

describe("reorder", () => {
  const withReview: Placeable[] = [
    ...SEED,
    { id: "s-review", name: "In review", category: "in_progress", position: 35 },
  ];

  it("swaps two positions and patches only those two", () => {
    const patches = reorder(withReview, "s-review", "up");
    expect(patches).toEqual([
      { id: "s-review", position: 30 },
      { id: "s-doing", position: 35 },
    ]);
  });

  it("refuses to move past the top of the category", () => {
    expect(reorder(withReview, "s-doing", "up")).toEqual([]);
  });

  it("refuses to move past the bottom of the category", () => {
    expect(reorder(withReview, "s-review", "down")).toEqual([]);
  });

  it("never moves a status into another category", () => {
    // "In progress" is first in its own category. Moving it up must NOT put an
    // in_progress lane left of the todo lane — the board is grouped by
    // lifecycle, and that reads backwards. Changing stage is the category
    // control's job.
    expect(reorder(SEED, "s-doing", "up")).toEqual([]);
    expect(reorder(SEED, "s-doing", "down")).toEqual([]);
  });

  it("renumbers when two siblings share a position", () => {
    // Swapping equal numbers changes nothing while name breaks the tie, so the
    // arrow would silently do nothing — on exactly the data a hand-written SQL
    // insert produces.
    const tied: Placeable[] = [
      { id: "a", name: "Alpha", category: "todo", position: 20 },
      { id: "b", name: "Beta", category: "todo", position: 20 },
    ];
    const patches = reorder(tied, "b", "up");
    expect(patches.length).toBeGreaterThan(0);
    const byId = new Map(patches.map((p) => [p.id, p.position]));
    const posOf = (id: string) => byId.get(id) ?? 20;
    expect(posOf("b")).toBeLessThan(posOf("a"));
  });

  it("returns nothing for an id it does not hold", () => {
    expect(reorder(SEED, "nope", "up")).toEqual([]);
  });

  it("does not mutate the rows it was handed", () => {
    const copy = withReview.map((r) => ({ ...r }));
    reorder(withReview, "s-review", "up");
    expect(withReview).toEqual(copy);
  });
});

describe("isLastInCategory", () => {
  it("is true when a category holds exactly one status", () => {
    // Deleting this one leaves a project that cannot complete a task.
    expect(isLastInCategory(SEED, "s-done")).toBe(true);
  });

  it("is false once the category holds a second", () => {
    const rows = [
      ...SEED,
      { id: "s-shipped", name: "Shipped", category: "done", position: 45 },
    ];
    expect(isLastInCategory(rows, "s-done")).toBe(false);
  });

  it("is false for an id it does not hold", () => {
    expect(isLastInCategory(SEED, "nope")).toBe(false);
  });
});

describe("emptyCategories", () => {
  it("names the gaps in lifecycle order", () => {
    expect(emptyCategories(SEED)).toEqual(["cancelled"]);
  });

  it("names every editable category when a project has no statuses", () => {
    expect(emptyCategories([])).toEqual([
      "backlog",
      "todo",
      "in_progress",
      "done",
      "cancelled",
    ]);
  });

  it("does not report triage as a gap", () => {
    // Owner directive 2026-09-03: triage is not offered, so its absence is not
    // a gap to nag about.
    expect(emptyCategories(SEED)).not.toContain("triage");
  });
});
