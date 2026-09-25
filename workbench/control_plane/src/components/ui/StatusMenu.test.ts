/**
 * The status menu's rules — D79, "stages group, statuses write".
 *
 * `vitest.config.ts` runs in `node`, so the component cannot render here.
 * Its keyboard and grouping rules are pure functions, and this file pins
 * them: the grouping under stages, the disabled row for an empty stage, the
 * drag prompt's one-stage filter, and focus that skips the headings.
 */
import { describe, expect, it } from "vitest";

import {
  type MenuRow,
  type StatusOption,
  initialFocus,
  menuRows,
  statusDot,
  stepFocus,
  typeAhead,
} from "./StatusMenu";

const s = (id: string, name: string, category: string, position: number, color?: string) =>
  ({ id, name, category, position, color }) as StatusOption;

/** Engineering: a violet Review beside Doing, out of order on purpose. */
const ENGINEERING = [
  s("done", "Done", "done", 40, "green"),
  s("review", "Review", "in_progress", 35, "violet"),
  s("todo", "To do", "todo", 20),
  s("doing", "Doing", "in_progress", 30),
  s("backlog", "Backlog", "backlog", 10),
];

const shape = (rows: MenuRow[]) =>
  rows.map((r) => (r.kind === "status" ? r.status.id : `${r.kind}:${r.category}`));

describe("menuRows — statuses grouped under their stage", () => {
  it("draws every stage in lifecycle order, statuses in board order", () => {
    expect(shape(menuRows(ENGINEERING))).toEqual([
      "stage:backlog",
      "backlog",
      "stage:todo",
      "todo",
      "stage:in_progress",
      "doing",
      "review",
      "stage:done",
      "done",
      "stage:cancelled",
      "empty:cancelled",
    ]);
  });

  it("says an empty stage in one disabled row", () => {
    const empty = menuRows(ENGINEERING).find((r) => r.kind === "empty");
    expect(empty).toMatchObject({ kind: "empty", label: "No cancelled status" });
  });

  it("keeps one stage for the drag prompt", () => {
    expect(shape(menuRows(ENGINEERING, "in_progress"))).toEqual([
      "stage:in_progress",
      "doing",
      "review",
    ]);
  });
});

describe("focus — opens on the right row, and skips the headings", () => {
  const rows = menuRows(ENGINEERING);
  const idOf = (at: number) => {
    const row = rows[at];
    return row.kind === "status" ? row.status.id : row.kind;
  };

  it("opens on the current status", () => {
    expect(idOf(initialFocus(rows, "review"))).toBe("review");
  });

  it("opens on focusId when the caller names one (the drag prompt)", () => {
    const prompt = menuRows(ENGINEERING, "in_progress");
    const at = initialFocus(prompt, "todo", "doing");
    expect(prompt[at]).toMatchObject({ kind: "status", status: { id: "doing" } });
  });

  it("opens on the first status when the current one is not listed", () => {
    expect(idOf(initialFocus(rows, "gone"))).toBe("backlog");
  });

  it("steps over stage headings and the empty row, and wraps", () => {
    const at = initialFocus(rows, "todo");
    expect(idOf(stepFocus(rows, at, 1))).toBe("doing");
    expect(idOf(stepFocus(rows, at, -1))).toBe("backlog");
    const last = initialFocus(rows, "done");
    expect(idOf(stepFocus(rows, last, 1))).toBe("backlog");
  });

  it("type-ahead jumps to the next status by its first letters", () => {
    const at = initialFocus(rows, "backlog");
    expect(idOf(typeAhead(rows, at, "d"))).toBe("doing");
    expect(idOf(typeAhead(rows, initialFocus(rows, "doing"), "d"))).toBe("done");
    expect(idOf(typeAhead(rows, at, "RE"))).toBe("review");
    expect(typeAhead(rows, at, "x")).toBe(-1);
  });
});

describe("the colour dot — the one status vocabulary", () => {
  it("reads the stored colour first, then the stage", () => {
    expect(statusDot(ENGINEERING[1])).toBe(statusDot(s("x", "x", "todo", 0, "violet")));
    expect(statusDot(s("x", "Doing", "in_progress", 0))).not.toBe(
      statusDot(s("y", "Review", "in_progress", 0, "violet")),
    );
  });
});
