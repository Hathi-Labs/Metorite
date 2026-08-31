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
