/**
 * The status menu's rules — D79, "stages group, statuses write".
 *
 * `vitest.config.ts` runs in `node`, so the component cannot render here.
 * Its keyboard and grouping rules are pure functions, and this file pins
 * them: the grouping under stages, the disabled row for an empty stage, the
 * drag prompt's one-stage filter, and focus that skips the headings.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  type MenuRow,
  type StatusOption,
  initialFocus,
  menuHeight,
  menuRows,
  rootRemPx,
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

describe("menuHeight — the panel hangs by its real height (D79)", () => {
  it("a short drag prompt measures short, so it opens beside its card", () => {
    const prompt = menuRows(ENGINEERING, "in_progress");
    expect(menuHeight(prompt.length, true)).toBeLessThan(160);
  });

  it("never grows past 320", () => {
    expect(menuHeight(40, true)).toBe(320);
  });

  it("grows with the density, so a short menu does not scroll at a roomier one", () => {
    // Density scales the root font size (`--ui-scale`), and every row with
    // it. The px estimate stayed at 26 a row, and at 20px a row is 32.5.
    const rows = menuRows(ENGINEERING, "in_progress").length;
    const roomy = menuHeight(rows, true, 20);
    expect(roomy).toBe(Math.ceil((0.75 + 2.5 + rows * 1.625) * 20));
    expect(roomy).toBeGreaterThan(menuHeight(rows, true, 16));
    expect(menuHeight(rows, true, 12)).toBeLessThan(menuHeight(rows, true, 16));
  });

  it("caps at 20rem at every density, the max-h-80 the panel carried", () => {
    expect(menuHeight(40, true, 20)).toBe(400);
    expect(menuHeight(40, true, 12)).toBe(240);
  });

  it("reads the root font size, and 16 where it cannot", () => {
    const doc = { documentElement: {} as HTMLElement };
    expect(rootRemPx(doc, () => ({ fontSize: "20px" }))).toBe(20);
    expect(rootRemPx(doc, () => ({ fontSize: "" }))).toBe(16);
    expect(rootRemPx(undefined)).toBe(16);
  });

  it("the menu passes the root font size to its height, read only while open", () => {
    const src = readFileSync(fileURLToPath(new URL("./StatusMenu.tsx", import.meta.url)), "utf8");
    // A closed menu mounts on every card, so it must not read a style.
    expect(src).toContain("const remPx = open ? rootRemPx() : 16;");
    expect(src.match(/rootRemPx\(\)/g)).toHaveLength(1);
    expect(src).toContain(
      "maxHeight={menuHeight(rows.length, Boolean(projectName || prompt), remPx)}",
    );
  });
});
