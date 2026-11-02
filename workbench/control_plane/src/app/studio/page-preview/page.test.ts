/**
 * STU-D11b fence: the page preview route is gated, no pane lists it, and the
 * hook that reads the URL sits inside a Suspense boundary.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { featureForPath } from "@/lib/access";
import { PANES } from "@/lib/nav";
import { buildPage } from "@/lib/studio/pageModel";
import samplePage from "@/lib/studio/samplePage.json";
import type { StudioManifest } from "@/lib/studio/manifest";

const SRC = readFileSync(new URL("./page.tsx", import.meta.url), "utf-8");

describe("/studio/page-preview", () => {
  it("is gated by the feature of /studio", () => {
    const studio = featureForPath("/studio");
    expect(studio).toBeTruthy();
    expect(featureForPath("/studio/page-preview")).toBe(studio);
  });

  it("is in no nav pane", () => {
    expect(PANES.some((p) => p.href.startsWith("/studio/page-preview"))).toBe(false);
  });

  it("wraps the URL reader in a Suspense boundary", () => {
    expect(SRC).toMatch(/<Suspense[^>]*>\s*<PreviewBody \/>\s*<\/Suspense>/);
  });

  it("says that the data is sample data, and uses the fake door", () => {
    expect(SRC).toContain("Sample data");
    expect(SRC).toContain("makeSampleDoor");
  });

  it("mounts no shell part of its own", () => {
    expect(SRC).not.toMatch(/NotificationBell|CommandPalette|AgentChat|AssistantDock|keydown/);
  });

  it("shows a sample page with two tabs, a span and a side view", () => {
    const m = samplePage as unknown as StudioManifest;
    const b = buildPage(m);
    expect(b.page.tabs).toHaveLength(2);
    expect(b.page.side).toBe("visit-detail");
    expect(b.cells.some((c) => c.span > 1)).toBe(true);
    expect(buildPage(m, null, "plan").cells.map((c) => c.view)).toEqual(["visit-board"]);
  });
});
