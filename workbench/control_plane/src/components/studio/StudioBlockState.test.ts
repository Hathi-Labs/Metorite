/**
 * STU-LY-1 fence: the component draws each shared state from the design-system parts, and a layout
 * preset keeps no copy of a state of its own.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import StudioBlockState, { type StudioBlockStateProps } from "@/components/studio/StudioBlockState";

const base: StudioBlockStateProps = {
  loading: false, error: null, forbidden: false, rows: 0, filtersActive: false, noun: "visits", create: "visit",
};
const draw = (p: Partial<StudioBlockStateProps>) => renderToStaticMarkup(createElement(StudioBlockState, { ...base, ...p }));

describe("StudioBlockState", () => {
  it("draws nothing when the block has rows", () => {
    expect(draw({ rows: 2 })).toBe("");
  });
  it("draws the loading skeleton with a busy status", () => {
    const html = draw({ loading: true });
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("animate-pulse");
  });
  it("draws the empty state with the create action", () => {
    const html = draw({ onCreate: () => {} });
    expect(html).toContain("No visits yet.");
    expect(html).toMatch(/<button[^>]*>[^<]*New visit/);
  });
  it("draws Clear filters on a filtered empty", () => {
    expect(draw({ filtersActive: true, onClearFilters: () => {} })).toMatch(/<button[^>]*>[^<]*Clear filters/);
  });
  it("draws an alert with Retry and never the server text", () => {
    const html = draw({ error: "boom 500", onRetry: () => {} });
    expect(html).toContain('role="alert"');
    expect(html).toMatch(/<button[^>]*>[^<]*Retry/);
    expect(html).not.toContain("boom");
  });
  it("draws no-permission with no button", () => {
    const html = draw({ forbidden: true, askWho: "an admin" });
    expect(html).toContain("You cannot open the visits.");
    expect(html).toContain("Ask an admin for access.");
    expect(html).not.toContain("<button");
  });
  it("adds no colour of its own", () => {
    const src = readFileSync(join(__dirname, "StudioBlockState.tsx"), "utf-8");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b(?:bg|text|border)-(?:red|green|blue|amber|yellow|gray|slate)-/);
  });
});

describe("no local copy of a state in a layout preset", () => {
  it("keeps skeletons, retry buttons and empty sentences out of the layout files", () => {
    const dir = join(__dirname, "layouts");
    if (!existsSync(dir)) return;
    for (const f of readdirSync(dir).filter((n) => /\.tsx$/.test(n) && !/\.test\./.test(n))) {
      const src = readFileSync(join(dir, f), "utf-8");
      expect(src, f).not.toMatch(/animate-pulse|SkeletonRows|from "@\/components\/EmptyState"|>\s*Retry\s*</);
    }
  });
});
