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
    expect(ROW).toMatch(/group relative flex items-center/);
  });
  it("paints the hover and the active background on the whole row, not on the title Button", () => {
    const li = /<li\s+className=\{`([^`]*)`\}\s+data-session-row/.exec(ROW)?.[1] ?? "";
    expect(li).toMatch(/rounded-md/);
    expect(li).toMatch(/hover:bg-secondary/);
    expect(li).toMatch(/bg-primary\/10/);
    const btn = /layout="flex items-center"[\s\S]*?className=\{`([^`]*)`/.exec(ROW)?.[1] ?? "";
    expect(btn).not.toMatch(/bg-primary\/10|bg-sidebar-accent/);
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
    for (const name of ["Pencil", "Archive", "ArchiveRestore", "Trash2", "Pin", "PinOff"]) {
      expect(STRIP).toContain(`icon="${name}"`);
    }
    expect(STRIP).not.toMatch(/absolute/);
  });
  it("keeps the strip for a row that can only pin (a guest of a room has no archive)", () => {
    // `data-has-actions` is what folds the dot and the age away on hover. A row whose only
    // act is Pin must count it, or the strip hides and Pin never shows (CO-11, task 3510).
    const has = /const hasActions = ([^;]*);/.exec(ROW)?.[1] ?? "";
    expect(has).toMatch(/\bonPin\b/);
    expect(has).toMatch(/\bonArchive\b/);
    expect(has).toMatch(/isOwner !== false/);
    expect(STRIP_CLASS).toMatch(/\$\{hasActions \? "" : "hidden"\}/);
  });
});

describe("the touch row (task 3566)", () => {
  // Mutation: drop `pointer-coarse:hidden` from the strip and a sticky hover
  // shows the icons on a phone again. Fails here.
  it("hides the hover strip on touch with the shared variant, and keeps its desktop classes", () => {
    expect(STRIP_CLASS).toMatch(/pointer-coarse:hidden/);
    expect(STRIP_CLASS).toMatch(/rail-row-actions/);
    expect(STRIP_CLASS).toMatch(/reveal-on-hover/);
  });
  // Mutation: a desktop-visible More button (no `hidden`) fails here.
  it("draws one More button, shown on touch only, never a new media query", () => {
    const more = ROW.slice(ROW.indexOf("data-session-more"));
    expect(/data-session-more=""\s+className="([^"]*)"/.exec(ROW)?.[1]).toMatch(/hidden.*pointer-coarse:flex/);
    expect(more).toContain('icon="MoreHorizontal"');
    expect(more).toContain("More actions for ${title}");
    // The stop sits in the click handler of the More button itself (the span
    // up to its close), so a tap never selects the row. Mutation: remove it
    // from that handler and this goes red, whatever else in the file stops.
    const button = more.slice(0, more.indexOf("</span>"));
    expect(button).toMatch(/onClick=\{\(e\) => \{\s*e\.stopPropagation\(\);/);
    expect(more).toContain("<ContextMenu");
    expect(SRC).not.toMatch(/@media|matchMedia/);
  });
  it("puts the More button after the strip, so the desktop DOM order is unchanged", () => {
    expect(ROW.indexOf("data-session-more")).toBeGreaterThan(ROW.indexOf('data-session-acts=""'));
  });
});

describe("the age and the dot of a touch row (task 3566)", () => {
  const CSS = readFileSync(join(__dirname, "../../app/globals.css"), "utf-8").replace(/\r\n/g, "\n");
  const block = CSS.slice(CSS.indexOf("@variant pointer-coarse {\n  .group[data-session-row]"));
  // Mutation: remove the block, and a sticky :hover on a tapped row blanks the
  // age and the run dot while the strip is hidden too: this test goes red.
  it("keeps the age and the dot in place on touch, through the shared variant only", () => {
    expect(block.startsWith("@variant pointer-coarse {")).toBe(true);
    expect(block).toContain("[data-has-actions]:hover .rail-row-meta");
    expect(block).toMatch(/clip-path: none;/);
    expect(block).toMatch(/position: static;/);
  });
  it("does not widen to other rows: it names the session row", () => {
    expect(block.slice(0, 200)).toContain("[data-session-row]");
  });
});
