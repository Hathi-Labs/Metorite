/**
 * STU-AF2b fence: the layout shell of a Studio app.
 *
 * Mutation 1: import `buildPage` into the frame. "holds no page model" turns red.
 * Mutation 2: draw a row with a `<button`. "draws the rows with RailRow" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (url: string) => readFileSync(new URL(url, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const frame = strip(read("./StudioAppFrame.tsx"));

describe("StudioAppFrame", () => {
  it("draws the rows with RailRow", () => {
    expect(frame).toContain('import RailRow from "@/components/ui/RailRow"');
    expect(frame).toContain("<RailRow");
    expect(frame).not.toContain("<button");
  });

  it("holds no page model", () => {
    expect(frame).not.toMatch(/pageModel|buildPage|viewBlocks|mapView|StudioPage/);
  });

  it("draws labels as text and owns no colour", () => {
    expect(frame).not.toContain("dangerouslySetInnerHTML");
    expect(frame).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(/);
  });

  it("mounts no shell part of its own", () => {
    expect(frame).not.toMatch(/NotificationBell|AgentChat|keydown|CommandPalette/);
  });

  it("folds as the CRM does and opens the phone drawer", () => {
    expect(frame).toContain("useRailFold");
    expect(frame).toContain("railClass(");
    expect(frame).toContain("useMobileDrawer");
  });

  it("is the one frame of AppFrame, and StudioPage draws bare inside it", () => {
    const app = read("../../app/studio/package-preview/AppFrame.tsx");
    expect(app).toContain("<StudioAppFrame");
    expect(app).toContain("<StudioPage bare");
    expect(app).not.toContain('label="Page"');
    const page = read("./StudioPage.tsx");
    expect(page).toContain("bare = false");
    expect(read("../../app/studio/page-preview/page.tsx")).not.toContain("bare");
  });

  it("keeps the owner verbs out of the phone bar", () => {
    const app = read("../../app/studio/package-preview/AppFrame.tsx");
    expect(app).toContain("useViewMode");
    expect(app).toContain("actions={isMobile ? undefined : ownerBar}");
    expect(app).toContain("{isMobile ? ownerBar : null}");
  });
});
