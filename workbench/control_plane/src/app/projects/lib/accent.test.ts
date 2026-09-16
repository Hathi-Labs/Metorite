/**
 * Projects · the facts this app feeds the shared colour vocabulary (WS-27ad).
 *
 * The palette and its precedence are pinned in `src/lib/statusAccent.test.ts`.
 * What is pinned here is the translation — and specifically the two ways it
 * could quietly go wrong: a stored colour being ignored (which is the bug this
 * ticket exists to fix — `pm_task_statuses.color` had been stored and never
 * drawn since migration 146), and a non-status axis being read for meaning it
 * does not have.
 */

import { describe, expect, it } from "vitest";

import { statusAccent } from "@/lib/statusAccent";

import { accentForGroup, accentForStatus } from "./accent";
import type { StatusRow } from "./api";

const status = (over: Partial<StatusRow>): StatusRow => ({
  id: "s1",
  project_id: "p1",
  name: "To do",
  color: "gray",
  position: 10,
  category: "todo",
  is_default: false,
  ...over,
});

describe("accentForStatus", () => {
  it("draws the colour the owner stored", () => {
    // The whole point of the ticket: this column used to be `bg-muted` like
    // every other, whatever the row said.
    expect(accentForStatus(status({ color: "green", category: "todo" }))).toEqual(
      statusAccent({ color: "green" }),
    );
  });

  it("falls back to the category when no colour is stored", () => {
    expect(accentForStatus(status({ color: "", category: "in_progress" }))).toEqual(
      statusAccent({ category: "in_progress" }),
    );
  });

  it("never reads the status NAME", () => {
    // A Projects lane called "Waiting on legal" in the `todo` category is a
    // todo lane. /tasks guesses from names because it has nothing better;
    // Projects has a category and must not second-guess it.
    const named = accentForStatus(status({ name: "Waiting on legal", color: "", category: "todo" }));
    expect(named).toEqual(statusAccent({ category: "todo" }));
    expect(named).not.toEqual(statusAccent({ name: "Waiting on legal" }));
  });

  it("is positional when there is no status at all", () => {
    expect(accentForStatus(undefined, 1, 4)).toEqual(statusAccent({ index: 1, total: 4 }));
  });
});

describe("accentForGroup", () => {
  const statuses = [
    status({ id: "todo", color: "blue", category: "todo" }),
    status({ id: "done", color: "green", category: "done" }),
  ];

  it("resolves a status column through its row", () => {
    expect(accentForGroup("status", "done", 1, 2, statuses)).toEqual(
      statusAccent({ color: "green" }),
    );
  });

  it("is positional for the unset lane, which has no row", () => {
    expect(accentForGroup("status", "__unset__", 1, 3, statuses)).toEqual(
      statusAccent({ index: 1, total: 3 }),
    );
  });

  it("never reads a person's or a tag's name for meaning", () => {
    // "Mark Green" is a colleague, not a done lane.
    expect(accentForGroup("assignee", "Mark Green", 1, 3, statuses)).toEqual(
      statusAccent({ index: 1, total: 3 }),
    );
    expect(accentForGroup("tag", "blocked", 0, 3, statuses)).toEqual(
      statusAccent({ index: 0, total: 3 }),
    );
  });

  it("gives neighbouring lanes different hues off the status axis", () => {
    const hues = [0, 1, 2, 3].map(
      (index) => accentForGroup("assignee", `p${index}`, index, 4, statuses).dot,
    );
    expect(new Set(hues).size).toBe(4);
  });
});

describe("accentForGroup on the STAGE axis (§9.12.3)", () => {
  const NONE: never[] = [];

  it("paints each stage from CATEGORY_HUES, not from its position", () => {
    // The four stages, each asked for at a DIFFERENT index than its own, so a
    // positional fallback cannot accidentally agree.
    const backlog = accentForGroup("category", "backlog", 3, 4, NONE);
    const todo = accentForGroup("category", "todo", 2, 4, NONE);
    const prog = accentForGroup("category", "in_progress", 1, 4, NONE);
    const done = accentForGroup("category", "done", 0, 4, NONE);

    expect(backlog).toEqual(
      statusAccent({ category: "backlog", index: 3, total: 4 })
    );
    expect(todo).toEqual(
      statusAccent({ category: "todo", index: 2, total: 4 })
    );
    expect(prog).toEqual(
      statusAccent({ category: "in_progress", index: 1, total: 4 })
    );
    expect(done).toEqual(
      statusAccent({ category: "done", index: 0, total: 4 })
    );
  });

  it("gives a stage the SAME colour wherever its column sits", () => {
    // ⚠️ The regression this exists for. Adding a lane to an earlier stage
    // shifts every later column's index. If colour followed the index, a
    // project's Done column would change hue because somebody added a Backlog
    // lane — and two spaces would paint the same stage differently.
    const first = accentForGroup("category", "done", 0, 6, NONE);
    const later = accentForGroup("category", "done", 5, 6, NONE);
    expect(first).toEqual(later);
  });

  it("does not paint DONE the same as IN PROGRESS", () => {
    // Non-vacuity: a broken CATEGORY_HUES that returned one hue for
    // everything would pass both tests above.
    expect(accentForGroup("category", "done", 0, 4, NONE)).not.toEqual(
      accentForGroup("category", "in_progress", 0, 4, NONE)
    );
  });

  it("falls back positionally for a stage nobody has a hue for", () => {
    // A server ahead of this client. The column still draws, in a hue that
    // does not claim to mean anything.
    expect(accentForGroup("category", "hibernating", 2, 5, NONE)).toEqual(
      statusAccent({ index: 2, total: 5 })
    );
  });
});
