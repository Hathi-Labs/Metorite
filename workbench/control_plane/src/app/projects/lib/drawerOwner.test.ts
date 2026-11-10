/**
 * WS-64 #8 — the page never closes a drawer the shell's Menu opened.
 * The feed effect of `page.tsx` re-runs on every tree refetch, so the
 * decision is replayed here the way the effect replays it.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { drawerStep } from "./drawerOwner";

/** The effect, with the ref and the drawer as plain values. */
function run(sheets: Array<boolean>, menuOpensAt: number | null) {
  let owns = false;
  let drawerOpen = false;
  sheets.forEach((sheetOpen, i) => {
    if (menuOpensAt === i) drawerOpen = true; // the shell's Menu tab
    const step = drawerStep(sheetOpen, owns);
    if (step === "show") {
      owns = true;
      drawerOpen = true;
    } else if (step === "close") {
      owns = false;
      drawerOpen = false;
    }
  });
  return drawerOpen;
}

describe("drawerStep", () => {
  it("keeps the Menu drawer open across tree refetches", () => {
    // sheet is null on every run; the Menu opens at the second run.
    expect(run([false, false, false, false], 1)).toBe(true);
  });

  it("still closes a drawer the page opened itself", () => {
    expect(run([true, true, false], null)).toBe(false);
  });

  it("closes only once, and then leaves a later Menu alone", () => {
    expect(run([true, false, false, false], 2)).toBe(true);
  });

  it("the page uses the rule", () => {
    const page = fs.readFileSync(path.join(__dirname, "..", "page.tsx"), "utf-8");
    expect(page).toContain('drawerStep(false, ownsDrawer.current) === "close"');
  });
});
