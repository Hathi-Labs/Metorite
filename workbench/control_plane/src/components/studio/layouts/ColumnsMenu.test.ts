/**
 * STU-LY-2b-f1 fence (task 4115): the Columns menu gives the focus back to its button when it closes.
 * `ContextMenu` leaves the focus to its caller, so a close that only hides the menu drops the keyboard
 * on the page body. The render has no DOM here, so the fence reads the source; the browser check is on
 * the task.
 *
 * Mutation: pass `onClose={() => setAt(null)}` again. This test turns red.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const menu = readFileSync(join(__dirname, "ColumnsMenu.tsx"), "utf-8");

describe("ColumnsMenu focus", () => {
  it("closes through one handler that focuses the button", () => {
    expect(menu).toContain("onClose={close}");
    expect(menu).toMatch(/const close = \(\) => \{\s*setAt\(null\);\s*btn\.current\?\.querySelector<HTMLButtonElement>\("button"\)\?\.focus\(\);/);
  });
});
