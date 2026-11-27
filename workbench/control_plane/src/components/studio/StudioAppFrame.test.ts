/**
 * STU-AF2b fence: the layout shell of a Studio app.
 *
 * Mutation 1: import `buildPage` into the frame. "holds no page model" turns red.
 * Mutation 2: draw a row with a `<button`. "draws the rows with RailRow" turns red.
 * Mutation 3 (STU-D23a): set `data-floor` on the root with no check. "draws no data-floor and the
 * same markup without the floor view" turns red.
 * Mutation 4 (STU-D23a-2): drop the `FloorContext` provider. "gives the floor view to its
 * children" turns red. Add the touch classes to the back link always: "keeps the back link of
 * every other app byte for byte" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/studio/package-preview" }));
vi.mock("@/components/AppShell", () => ({ useMobileDrawer: () => ({ open: () => {}, close: () => {} }) }));

import StudioAppFrame, { type StudioAppFrameProps } from "./StudioAppFrame";
import { AppTopBar, BACK_LINK_FLOOR } from "@/components/AppTopBar";
import { useFloor } from "@/lib/floorContext";

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
    // The rail replaced the page picker. The floor view has no rail, so it alone draws one, and
    // only through `floorPages` (STU-D23a-2). `package-preview/AppFrame.test.ts` holds the markup.
    expect(app.match(/label="Page"/g) ?? []).toHaveLength(1);
    expect(app).toMatch(/\{floorPick \? \(\s*<SelectButton label="Page"/);
    expect(app).toContain("const floorPick = floorPages(parts, sections);");
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

describe("the floor view root (STU-D23a)", () => {
  const RAIL = { sections: [{ title: "", rows: [{ page: "a", label: "A", icon: "FileText" as const }] }], activePage: "a", onPick: () => {} };
  const draw = (props: Partial<StudioAppFrameProps>) => renderToStaticMarkup(
    createElement(StudioAppFrame, { title: "Shop jobs", rail: RAIL, children: createElement("i", { "data-child": "" }), ...props }));

  it("draws no data-floor and the same markup without the floor view", () => {
    const usual = draw({});
    expect(usual).not.toContain("data-floor");
    expect(draw({ floor: false })).toBe(usual);
  });

  it("puts data-floor on the root, and on nothing else", () => {
    const html = draw({ floor: true });
    expect(html.startsWith('<div data-floor="" class="flex h-full w-full flex-col overflow-hidden bg-background">')).toBe(true);
    expect(html.match(/data-floor/g)).toHaveLength(1);
  });

  it("changes no class of the frame for the floor view", () => {
    const classes = (html: string) => [...html.matchAll(/class="([^"]*)"/g)].map((m) => m[1]);
    expect(classes(draw({ floor: true }))).toEqual(classes(draw({})));
  });

  it("draws the scope line beside the name", () => {
    expect(draw({ subtitle: "Signed in as Asha Rao" })).toContain("Signed in as Asha Rao");
  });
});

describe("the floor context and the back link (STU-D23a-2)", () => {
  const BACK = { href: "/studio", label: "Back to Studio" };
  // The class of the back link before STU-D23a-2. Outside the floor view it stays byte for byte.
  const USUAL_LINK = 'class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground tech-transition hover:bg-secondary hover:text-foreground"';
  /** A child that draws what the context says. */
  const Probe = () => createElement("i", { "data-probe": useFloor() ? "floor" : "usual" });
  const drawBack = (props: Partial<StudioAppFrameProps>) => renderToStaticMarkup(
    createElement(StudioAppFrame, { title: "Shop jobs", back: BACK, children: createElement(Probe), ...props }));

  it("gives the floor view to its children, and nothing without it", () => {
    expect(drawBack({ floor: true })).toContain('data-probe="floor"');
    expect(drawBack({})).toContain('data-probe="usual"');
    expect(drawBack({ floor: false })).toContain('data-probe="usual"');
  });

  it("gives the back link the touch size in the floor view", () => {
    expect(BACK_LINK_FLOOR).toBe(" pointer-coarse:h-10 pointer-coarse:w-10");
    expect(drawBack({ floor: true })).toContain(`${USUAL_LINK.slice(0, -1)}${BACK_LINK_FLOOR}"`);
  });

  it("keeps the back link and the frame byte for byte without the floor view", () => {
    const usual = drawBack({});
    expect(usual).toContain(USUAL_LINK);
    expect(usual).not.toContain(BACK_LINK_FLOOR.trim());
    expect(drawBack({ floor: false })).toBe(usual);
  });

  it("keeps the back link of every other app byte for byte, with no provider", () => {
    const html = renderToStaticMarkup(createElement(AppTopBar, { title: "Teams", back: BACK }));
    expect(html).toContain(USUAL_LINK);
    expect(html).not.toContain("pointer-coarse");
  });
});
