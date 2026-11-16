/**
 * The acts of a chat row sit IN the row (task 3564, owner 2026-11-16).
 *
 * The fault: the strip of Rename and Archive was `absolute right-1` over the
 * row. On hover only the age hid, so the icons drew over the end of the
 * title and over the run or unread dot. The strip is now a flex child that
 * takes no width at rest (`rail-row-actions`), and the dot and the age fold
 * away with it (`rail-row-meta`). A new act is one more Button in the strip,
 * with no pixel offset to move.
 *
 * Fence (R7): this file reads the source. `e2e/session-picker-strip.spec.ts`
 * measures the boxes in a browser.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = readFileSync(join(__dirname, "SessionPicker.tsx"), "utf-8").replace(/\r\n/g, "\n");
const ROW = SRC.slice(SRC.indexOf("function SessionRow"), SRC.indexOf("export function SessionPicker"));
const STRIP = ROW.slice(ROW.indexOf('data-session-acts=""'));
const STRIP_CLASS = /data-session-acts=""\s+className=\{`([^`]*)`\}/.exec(ROW)?.[1] ?? "";

describe("the session row strip", () => {
  it("is a flex child of the row, never absolute over the title", () => {
    expect(STRIP_CLASS).not.toBe("");
    expect(STRIP_CLASS).not.toMatch(/\babsolute\b/);
    expect(STRIP_CLASS).not.toMatch(/\bright-\d/);
    expect(STRIP_CLASS).not.toMatch(/-translate-y/);
    expect(ROW).toMatch(/className="group relative flex items-center"/);
  });
  it("takes no width at rest and gives the room on hover and focus", () => {
    expect(STRIP_CLASS).toMatch(/\brail-row-actions\b/);
    expect(STRIP_CLASS).toMatch(/\breveal-on-hover\b/);
  });
  it("lets the title shrink and truncate", () => {
    expect(ROW).toMatch(/min-w-0 flex-1 gap-2 rounded-md/);
    expect(ROW).not.toMatch(/className=\{`w-full gap-2/);
  });
  it("folds the dot and the age away with the strip, together", () => {
    const meta = /className="rail-row-meta[^"]*">\s*<SessionRunDot[\s\S]*?relativeAge[\s\S]*?<\/span>\s*<\/span>/.exec(ROW);
    expect(meta).not.toBeNull();
    expect(ROW).toContain("data-has-actions");
    expect(ROW).not.toMatch(/group-hover:invisible|group-focus-within:invisible/);
  });
  it("holds every act as a direct Button of the one strip", () => {
    for (const name of ["Pencil", "Archive", "ArchiveRestore", "Trash2"]) {
      expect(STRIP).toContain(`icon="${name}"`);
    }
    expect(STRIP).not.toMatch(/absolute/);
  });
});
