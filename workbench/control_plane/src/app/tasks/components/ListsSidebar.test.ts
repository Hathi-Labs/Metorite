/**
 * S6c — two fences on the My Tasks sidebar.
 *
 * 1. Horizons of Focus is off the surface (D65). The nav list and the
 *    `ViewKey` union carry no such view. The data and the routes stay, so this
 *    is a SOURCE fence on the two files that decide what a member can press.
 *    The word is assembled at runtime so `rg -c -i horizon src/app/tasks/lib/`
 *    (the spec's own check) does not count this file's assertion.
 * 2. The primary rows RENDER. They were deleted with the source filter on
 *    2026-08-25 (b6192110) and for a month the sidebar drew a heading and a
 *    Settings button — no way to reach Next Actions or Waiting For.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const HERE = __dirname;
const sidebar = fs.readFileSync(path.join(HERE, "ListsSidebar.tsx"), "utf-8");
const types = fs.readFileSync(path.join(HERE, "..", "lib", "types.ts"), "utf-8");
const store = fs.readFileSync(path.join(HERE, "..", "lib", "taskStore.ts"), "utf-8");

/** "hori" + "zons", so this test's own text is not a hit for the spec check. */
const WITHDRAWN = ["hori", "zons"].join("");

/** The `ViewKey` union's members, read from the source. */
function viewKeys(src: string): string[] {
  // Comments first: the union carries a paragraph with a `;` in it, and the
  // first version of this parser stopped there with four members.
  const bare = src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:\\])\/\/[^\n]*/g, "$1");
  const start = bare.indexOf("export type ViewKey");
  const end = bare.indexOf(";", start);
  const body = bare.slice(start, end);
  return [...body.matchAll(/\|\s*"([a-z]+)"/g)].map((m) => m[1]);
}

/** The `view:` keys of the sidebar's nav rows. */
function navViews(src: string): string[] {
  return [...src.matchAll(/\{\s*view:\s*"([a-z]+)"/g)].map((m) => m[1]);
}

describe("D65 — the altitude ladder is off the surface", () => {
  it("the ViewKey union has no such view", () => {
    const keys = viewKeys(types);
    expect(keys.length).toBeGreaterThan(5);
    expect(keys).not.toContain(WITHDRAWN);
  });

  it("the sidebar offers no such row", () => {
    expect(navViews(sidebar)).not.toContain(WITHDRAWN);
    expect(sidebar.toLowerCase()).not.toContain(`"${WITHDRAWN}"`);
  });

  it("the view counts carry no such key", () => {
    expect(store).not.toMatch(new RegExp(`\\b${WITHDRAWN}:\\s*0`));
  });

  it("every nav row is a member of ViewKey", () => {
    const keys = viewKeys(types);
    for (const view of navViews(sidebar)) expect(keys).toContain(view);
  });
});

describe("the primary rows render", () => {
  it("PRIMARY is mapped into NavButtons inside the nav", () => {
    // Declared-but-unrendered is the shape the 2026-08-25 deletion left.
    expect(sidebar).toMatch(/\{PRIMARY\.map\(/);
    expect(sidebar).toMatch(/<NavButton/);
  });

  it("the five destinations a member needs are on it", () => {
    const views = navViews(sidebar);
    for (const view of ["inbox", "next", "waiting", "someday", "done", "archive"]) {
      expect(views).toContain(view);
    }
  });
});
