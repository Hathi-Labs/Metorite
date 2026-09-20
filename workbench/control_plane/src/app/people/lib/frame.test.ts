/**
 * The People app is ONE app, so it has ONE page width.
 *
 * Spec: `people_center_app.md` §5.0 · `DESIGN_SYSTEM.md`.
 *
 * **Measured, not supposed.** On 2026-09-20 the seven surfaces declared FOUR
 * widths — 3xl on Find skills, the working week, data quality and the org
 * chart; 4xl on My profile and the summary; 6xl on Workload. Rendering them
 * in order showed the heading's left edge move on four of the six tab
 * clicks. Nothing was broken and the app did not read as one app.
 *
 * CLAUDE.md §4 records that the conformance suite checks eight regexes and
 * **nothing tests layout or cross-app continuity**. This is the smallest
 * useful piece of that gap: a grep, so the next page inherits the frame
 * instead of choosing one.
 */

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { PAGE_FRAME, PAGE_FRAME_BLOCK } from "./frame";

const APP = path.join(__dirname, "..");

/**
 * The directory is the one page with no frame, and it is a different KIND of
 * page: a full-height two-pane layout with its own scroll region, not a
 * document that flows. Named here so the exception is a decision rather than
 * an oversight.
 */
const UNFRAMED = new Set(["page.tsx"]);

function pageFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "components" && entry.name !== "lib") walk(full);
      } else if (entry.name === "page.tsx") {
        out.push(full);
      }
    }
  };
  walk(APP);
  return out;
}

describe("the People app's page frame", () => {
  it("finds the pages at all", () => {
    // A walk that finds nothing makes every assertion below pass vacuously.
    const files = pageFiles();
    expect(files.length).toBeGreaterThanOrEqual(7);
  });

  it("declares one width, and no page invents its own", () => {
    const offenders: string[] = [];
    for (const file of pageFiles()) {
      const rel = path.relative(APP, file);
      const source = fs.readFileSync(file, "utf-8");
      const widths = source.match(/max-w-(?:screen-)?(?:\d?xl|\d+xl)/g) ?? [];
      // `max-w-prose` is deliberately allowed: a MEASURE is a property of the
      // paragraph, decided where the paragraph is, and it travels with it.
      if (widths.length > 0) offenders.push(`${rel}: ${widths.join(", ")}`);
    }
    expect(offenders, "a page must use PAGE_FRAME, never its own max-w").toEqual([]);
  });

  it("every flowing page uses the shared constant", () => {
    const missing: string[] = [];
    for (const file of pageFiles()) {
      const rel = path.relative(APP, file);
      if (UNFRAMED.has(rel)) continue;
      const source = fs.readFileSync(file, "utf-8");
      if (!source.includes("PAGE_FRAME")) missing.push(rel);
    }
    expect(missing).toEqual([]);
  });

  it("the two frames agree about the width", () => {
    // Two constants so a page that lays out its own children does not have to
    // fight `flex`. They must never disagree about the width itself.
    const width = (s: string) => s.match(/max-w-\S+/)?.[0];
    expect(width(PAGE_FRAME)).toBe(width(PAGE_FRAME_BLOCK));
    expect(width(PAGE_FRAME)).toBe("max-w-5xl");
  });
});
