/**
 * MOB-11 (task 2004): on a phone board, the status tabs are the first row.
 *
 * Source test: `vitest.config.ts` runs in `node`, with no DOM, so this pins
 * the condition on the Statuses row. The "Manage this project" menu in the
 * project title row holds the same Statuses item, so the row is a duplicate
 * on a phone. Desktop keeps it. `projectsSettings.test.ts` pins the menu item.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8").replace(/\r\n/g, "\n");

describe("the phone board's first row", () => {
  const board = read("./TaskBoard.tsx");

  it("draws the Statuses button row only when the screen is not a phone", () => {
    expect(board).toMatch(
      /\{onManageStatuses && groupBy === "status" && !phone \? \(\s*<div[^>]*>\s*<Button[^>]*icon="Columns3"[\s\S]*?Statuses\s*<\/Button>/,
    );
  });

  it("draws the Statuses button once, so no other row repeats it", () => {
    expect(board.match(/icon="Columns3"/g) ?? []).toHaveLength(1);
  });
});
