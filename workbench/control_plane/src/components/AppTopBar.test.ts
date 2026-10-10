/**
 * The app bar: ONE component, the title bar every app opens with.
 *
 * Until 2026-09-24 each app drew its own `h-10` bar, and the two drifted: My
 * Tasks had a hand-rolled rail toggle, no divider, its name in a `<span>`, and
 * no search and no bell. This file holds the bar to one shape, and holds the
 * two task apps to rendering it. `lib/shell/appBar.test.ts` holds every live
 * pane to it.
 *
 * Since 2026-10-10 (owner) the bar always draws its own row on desktop. It
 * never portals into the shell bar, which is constant across the product.
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

import { AppSearchButton, AppTopBar, appBarIcon, railLabel } from "./AppTopBar";

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
    expect(open).toContain('class="h-4 w-px shrink-0 bg-border"');
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

  it("draws its own row, in one order: toggle, icon, name, scope, actions, tools", () => {
    const html = bar({
      title: "Calendar",
      icon: "Calendar",
      subtitle: "SCOPE",
      rail: { open: true, onToggle: () => {}, noun: "the rail" },
      actions: createElement("span", null, "ACTION"),
      tools: createElement("span", null, "TOOL"),
    });
    // One row, h-10, marked for the browser suite.
    // One row, at least h-10, marked for the browser suite. It WRAPS when it
    // runs out of room (round 2), so a tool never leaves the screen.
    expect(html).toMatch(/^<div data-app-bar="desktop" class="flex min-h-10 [^"]*flex-wrap[^"]*"/);
    const at = (needle: string) => {
      const i = html.indexOf(needle);
      expect(i, needle).toBeGreaterThan(-1);
      return i;
    };
    const order = [
      at("Hide the rail"),
      at("lucide-calendar"),
      at("<h1"),
      at("SCOPE"),
      at("ACTION"),
      at("TOOL"),
    ];
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("takes the icon of the pane that owns the route, so the bar and the sidebar agree", () => {
    expect(appBarIcon("/tasks")).toBe("CheckSquare");
    expect(appBarIcon("/email")).toBe("Mail");
    // The longest pane wins: My Profile inside the People app.
    expect(appBarIcon("/people/me")).toBe("User");
    expect(appBarIcon("/people/chart")).toBe("Users");
    expect(appBarIcon("/settings/organization")).toBe("Building2");
    // `/` belongs to no pane. My Day names its icon itself.
    expect(appBarIcon("/")).toBeNull();
    expect(appBarIcon(null)).toBeNull();
    // `null` draws no icon at all.
    expect(bar({ title: "X", icon: null })).not.toContain("<svg");
  });

  it("never portals into the shell bar: the slot API is gone", () => {
    const src = code(read("components/AppTopBar.tsx"));
    expect(src).not.toMatch(/createPortal|useShellSlots|from "@\/lib\/shell\/ShellBar"/);
  });

  it("gives way in order: the scope line first, then the name with an ellipsis", () => {
    const html = bar({ title: "Organisation", subtitle: "Acme · 3 active of 4" });
    // The name keeps its width until the scope line is gone, and then it is
    // held to its box (`max-w-full`) and truncates. It never overflows into
    // the control beside it (round 2, measured at 1024px).
    expect(html).toMatch(/<h1 class="max-w-full shrink-0 truncate [^"]*">Organisation<\/h1>/);
    expect(html).toMatch(/<span class="ml-2 min-w-0 truncate [^"]*">Acme · 3 active of 4<\/span>/);
    // The tools may wrap among themselves at the right end.
    const tools = bar({ title: "X", tools: createElement("span", null, "TOOL") });
    expect(tools).toMatch(/<div class="ml-auto flex min-w-0 max-w-full flex-wrap [^"]*"><span>TOOL<\/span><\/div>/);
  });

  it("a sub-page's back link sits at the left end, before the name", () => {
    const html = bar({ title: "Teams", back: { href: "/settings/organization", label: "Back to Organisation" } });
    expect(html).toMatch(/<a [^>]*href="\/settings\/organization"/);
    expect(html).toContain('aria-label="Back to Organisation"');
    expect(html.indexOf("Back to Organisation")).toBeLessThan(html.indexOf("<h1"));
  });

  it("the phone bar keeps the scope line, after the name", () => {
    const html = bar({ compact: true, title: "Approvals", subtitle: "3 waiting" });
    expect(html).toMatch(/^<div data-app-bar="compact"/);
    expect(html.indexOf(">Approvals</h1>")).toBeLessThan(html.indexOf(">3 waiting</span>"));
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
