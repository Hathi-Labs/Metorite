/**
 * The app bar: ONE component, rendered by Projects and by My Tasks.
 *
 * Until 2026-09-24 each app drew its own `h-10` bar, and the two drifted: My
 * Tasks had a hand-rolled rail toggle, no divider, its name in a `<span>`, and
 * no search and no bell. This file holds the bar to one shape, and holds both
 * apps to rendering it.
 *
 * The runner is `environment: "node"`, so the markup is rendered to a string
 * (`renderToStaticMarkup`) and the pages are read as source.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AppSearchButton, AppTopBar, railLabel } from "./AppTopBar";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

const bar = (props: Parameters<typeof AppTopBar>[0]) =>
  renderToStaticMarkup(createElement(AppTopBar, props));

/** The opening tag of the button that carries this exact attribute value. */
function buttonWith(html: string, attr: string, value: string): string {
  for (const m of html.matchAll(/<button\b[^>]*>/g)) {
    if (m[0].includes(`${attr}="${value}"`)) return m[0];
  }
  return "";
}

/** Source with its comments removed, so prose about a heading is not one. */
const code = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/])\/\/[^\n]*/g, "");

describe("the app bar's shape", () => {
  it("names the app in the page's one <h1>", () => {
    const html = bar({ title: "My Tasks" });
    expect(html.match(/<h1\b/g) ?? []).toHaveLength(1);
    expect(html).toMatch(/<h1 class="[^"]*">My Tasks<\/h1>/);
  });

  it("draws the rail toggle as a pressed Button, then a divider", () => {
    const open = bar({
      title: "My Tasks",
      rail: { open: true, onToggle: () => {}, noun: "your lists" },
    });
    // The primitive, not a hand-rolled button: `cc-control` is its class.
    const toggle = buttonWith(open, "aria-label", "Hide your lists");
    expect(toggle).toContain("cc-control");
    expect(toggle).toContain('aria-pressed="true"');
    expect(open).toContain('class="h-4 w-px bg-border"');
    // The toggle comes before the title, and the divider between them.
    expect(open.indexOf("Hide your lists")).toBeLessThan(open.indexOf("h-4 w-px"));
    expect(open.indexOf("h-4 w-px")).toBeLessThan(open.indexOf("<h1"));

    const shut = bar({
      title: "My Tasks",
      rail: { open: false, onToggle: () => {}, noun: "your lists" },
    });
    expect(buttonWith(shut, "aria-label", "Show your lists")).toContain('aria-pressed="false"');
  });

  it("puts the tools at the right end, after the actions", () => {
    const html = bar({
      title: "Projects",
      subtitle: "Every space you can see",
      actions: createElement("span", null, "ACTION"),
      tools: createElement("span", null, "TOOL"),
    });
    expect(html).toContain("Every space you can see");
    expect(html.indexOf("ACTION")).toBeLessThan(html.indexOf("TOOL"));
    expect(html).toMatch(/<div class="ml-auto[^"]*"><span>TOOL<\/span><\/div>/);
  });

  it("the phone bar has no rail and titles what you are looking at", () => {
    const html = bar({
      compact: true,
      title: "Ops board",
      rail: { open: true, onToggle: () => {}, noun: "the project tree" },
    });
    expect(html).not.toContain("the project tree");
    expect(html).toMatch(/<h1 class="[^"]*text-sm font-medium text-foreground">Ops board<\/h1>/);
  });

  it("says what a press of the toggle will do", () => {
    expect(railLabel({ open: true, noun: "the project tree" })).toBe("Hide the project tree");
    expect(railLabel({ open: false, noun: "the project tree" })).toBe("Show the project tree");
  });

  it("the search trigger is one Button with one label", () => {
    const html = renderToStaticMarkup(createElement(AppSearchButton, { onOpen: () => {} }));
    expect(buttonWith(html, "title", "Search every project (⌘K)")).toContain("cc-control");
    expect(html).toContain("Search");
  });
});

describe("both apps render it", () => {
  const PAGES = [
    { name: "Projects", file: "app/projects/page.tsx" },
    { name: "My Tasks", file: "app/tasks/page.tsx" },
  ];

  for (const page of PAGES) {
    it(`${page.name} draws its bar through AppTopBar, and no bar of its own`, () => {
      const src = code(read(page.file));
      expect(src).toMatch(/import \{ AppSearchButton, AppTopBar \} from "@\/components\/AppTopBar";/);
      expect(src).toMatch(/<AppTopBar\b/);
      // The hand-rolled bar these replaced. A second one is the drift back.
      expect(src).not.toMatch(/flex h-10 shrink-0 items-center/);
      expect(src).not.toMatch(/<h1\b/);
    });

    it(`${page.name} carries search and the bell in the bar`, () => {
      const src = read(page.file);
      const start = src.lastIndexOf("<AppTopBar");
      const end = src.indexOf("/>\n\n", src.indexOf("tools={", start));
      const desktop = src.slice(start, end);
      expect(desktop).toMatch(/<AppSearchButton onOpen=\{/);
      expect(desktop).toMatch(/<NotificationBell onOpenTask=\{/);
    });
  }

  it("My Tasks has no hand-rolled rail toggle left", () => {
    const src = code(read("app/tasks/page.tsx"));
    expect(src).not.toMatch(/function PanelToggle\b/);
    expect(src).not.toMatch(/<button\b/);
  });

  it("the My Tasks lists rail follows useRailFold, so it folds below lg", () => {
    const src = read("app/tasks/page.tsx");
    expect(src).toMatch(/const lists = useRailFold\(\);/);
    expect(src).toMatch(/rail=\{\{ open: lists\.open, onToggle: lists\.toggle, noun: "your lists" \}\}/);
    expect(src).toMatch(/\{lists\.open && \(\s*<aside/);
  });

  it("the lists sidebar names the app only in the phone drawer", () => {
    const sidebar = read("app/tasks/components/ListsSidebar.tsx");
    // Untitled unless asked: the desktop rail passes nothing.
    expect(sidebar).toMatch(/\btitled = false,\r?\n/);
    expect(sidebar).toMatch(/\{titled \? \(\s*<h2[^>]*>My Tasks<\/h2>\s*\) : null\}/);
    const page = read("app/tasks/page.tsx");
    expect(page).toMatch(/openDrawer\(<ListsSidebar titled onNavigate=\{closeDrawer\} \/>\)/);
    // The desktop rail is untitled: the bar above it owns the name.
    expect(page).toMatch(/<ListsSidebar\s+onOpenAssistant=/);
  });
});
