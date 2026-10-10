/**
 * The shell bar is constant. Every app opens with its own title bar.
 *
 * Owner direction, 2026-10-10: "I don't want to have any of the individual
 * apps' UI/UX elements be on the top bar, and the top bar is something that
 * is constant across the entire Metorite application." It reversed NS-1's
 * merged row, in which `AppTopBar` portalled an app's name and tools into
 * slots in the shell bar (`navigation_shell.md` §3.1, §5.2 rule 3, §13.3 Q4).
 *
 * The rule, in `DESIGN_SYSTEM.md` §6a and `AGENTS.md` rule 11: the shell bar
 * holds only shell things, an app never renders into it, and every app opens
 * with `AppTopBar`, its one title bar.
 *
 * Three fences, one for each way the rule breaks:
 *
 *   (a) The slot API is gone, and nothing outside `lib/shell/` reaches into
 *       the shell bar. A new slot, or a portal aimed at `[data-shell-bar]`,
 *       is the merged row coming back.
 *   (b) Every live pane renders `AppTopBar`, through its route. A new live
 *       pane with no title bar fails here by name, because the table below
 *       must hold exactly the live set of `nav.ts`.
 *   (c) The shell bar's markup holds no app name and no slot. Two apps draw
 *       the same bar, except for the command bar's own "in <App>" chip.
 *
 * The runner is `environment: "node"`, so (a) and (b) read source, and (c)
 * renders the row to a string. `e2e/shell-bar.spec.ts` checks the same rule
 * in a browser.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LIVE_PANES } from "@/lib/nav";

import { ShellBarRow } from "./ShellBar";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/** Source with comments removed, so prose about a slot is not a slot. */
const code = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/\\])\/\/[^\n]*/g, "");

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(SRC);
  return out.sort();
}

const FILES = sourceFiles();
const SHELL_HOME = "lib/shell/";

describe("(a) nothing renders into the shell bar", () => {
  it("finds the source at all", () => {
    // A walk that finds nothing makes every assertion below vacuous.
    expect(FILES.length).toBeGreaterThan(300);
  });

  it("the shell bar has no slot API left", () => {
    const bar = code(read("lib/shell/ShellBar.tsx"));
    expect(bar).not.toMatch(/\buseShellSlots\b|\bShellSlots\b|\bSlotsContext\b|\bclaim\b/);
    // A slot was an element the bar handed out by ref.
    expect(bar).not.toMatch(/ref=\{set/);
    expect(bar).not.toMatch(/createContext|createPortal/);
  });

  it("no file outside the shell names a slot or aims at the shell bar", () => {
    const offenders = FILES.filter((rel) => !rel.startsWith(SHELL_HOME)).filter((rel) =>
      /\buseShellSlots\b|\bShellSlots\b|data-shell-bar/.test(code(read(rel))),
    );
    expect(offenders).toEqual([]);
  });

  it("only AppShell imports the shell bar, and only its frame", () => {
    const importers = FILES.filter((rel) => !rel.startsWith(SHELL_HOME)).filter((rel) =>
      /from\s+["']@\/lib\/shell\/ShellBar["']/.test(read(rel)),
    );
    expect(importers).toEqual(["components/AppShell.tsx"]);
    const line = read("components/AppShell.tsx").match(/import\s*\{([^}]*)\}\s*from\s+["']@\/lib\/shell\/ShellBar["']/);
    expect(line?.[1].trim()).toBe("ShellFrame");
  });
});

/**
 * Where each live pane draws its title bar: the route's page or layout, or
 * the one component the page renders.
 *
 * ⚠️ The keys MUST equal the live set (`LIVE_PANES`). A pane that goes live
 * lands here in the same pull request, with the file that draws its bar.
 * There is no allow-list: every live pane can draw the bar, so every one
 * does.
 */
const APP_BAR_HOME: Record<string, string> = {
  "/tasks": "app/tasks/page.tsx",
  "/calendar": "app/calendar/CalendarView.tsx",
  // `/people/me` sits under the People layout, which names its bar from the
  // pane that owns the route (`appBarPane`): "My Profile" here.
  "/people/me": "app/people/layout.tsx",
  "/email": "app/email/page.tsx",
  "/whatsapp": "app/whatsapp/layout.tsx",
  "/projects": "app/projects/page.tsx",
  "/people": "app/people/layout.tsx",
  "/chat": "app/chat/page.tsx",
  "/approvals": "app/approvals/page.tsx",
  "/settings/organization": "app/settings/organization/OrganizationAdmin.tsx",
  "/settings/appearance": "app/settings/appearance/page.tsx",
};

/** My Day at `/`, behind `NEXT_PUBLIC_MY_DAY`. Not a nav pane, so apart. */
const MY_DAY_HOME = "lib/shell/MyDayPage.tsx";

/** The route's page, its layout, and the layout of every route above it. */
function routeFiles(href: string): string[] {
  const out: string[] = [];
  let dir = `app${href}`;
  out.push(`${dir}/page.tsx`);
  while (dir.startsWith("app")) {
    out.push(`${dir}/layout.tsx`);
    if (dir === "app") break;
    dir = dirname(dir).split(sep).join("/");
  }
  return out.filter((rel) => existsSync(join(SRC, rel)));
}

/** True when `from` imports `target` by a path that ends in its name. */
function imports(from: string, target: string): boolean {
  const stem = target.replace(/\.tsx?$/, "").split("/").pop()!;
  const re = new RegExp(String.raw`from\s+["'][^"']*\/${stem}["']`);
  return re.test(read(from));
}

function drawsTheBar(rel: string): void {
  // The import is read from the raw source, anchored at a line start, so a
  // docstring cannot satisfy it. The tag is read with comments removed.
  expect(read(rel), `${rel} imports the one app bar`).toMatch(
    /^import\s*\{[^}]*\bAppTopBar\b[^}]*\}\s*from\s+["']@\/components\/AppTopBar["'];/m,
  );
  expect(code(read(rel)), `${rel} renders it`).toMatch(/<AppTopBar\b/);
}

describe("(b) every live pane opens with AppTopBar", () => {
  it("the table holds exactly the live set", () => {
    expect(Object.keys(APP_BAR_HOME).sort()).toEqual(LIVE_PANES.map((p) => p.href).sort());
  });

  for (const [href, rel] of Object.entries(APP_BAR_HOME)) {
    it(`${href} draws its bar in ${rel}, which its route renders`, () => {
      drawsTheBar(rel);
      const route = routeFiles(href);
      const reached = route.includes(rel) || route.some((r) => imports(r, rel));
      expect(reached, `${href}: none of ${route.join(", ")} reaches ${rel}`).toBe(true);
    });
  }

  it("My Day at / draws its bar too", () => {
    drawsTheBar(MY_DAY_HOME);
    expect(imports("app/page.tsx", MY_DAY_HOME)).toBe(true);
  });

  it("no live pane draws a hand-rolled title row beside the bar", () => {
    // The shapes the bar replaced: Calendar's own h1, Approvals' text-lg
    // h1, Email's folder h1 and the WhatsApp rail's name. A pane under the
    // bar titles itself with an h2.
    for (const rel of new Set([...Object.values(APP_BAR_HOME), MY_DAY_HOME])) {
      let src = code(read(rel));
      // One named exception. A member without `admin:members:read` gets a
      // denial card IN PLACE of Organisation, so no bar shows above it, and
      // the card keeps the page's one h1 (`pageHeading.test.ts` pins it).
      if (rel === "app/settings/organization/OrganizationAdmin.tsx") {
        src = src.replace(/<h1 className="[^"]*">\s*Organisation is admin-only\s*<\/h1>/, "");
      }
      expect(src, rel).not.toMatch(/<h1[\s>]/);
    }
  });
});

describe("(c) the shell bar holds no app", () => {
  const row = (here: { label: string; icon: string } | null) =>
    renderToStaticMarkup(createElement(ShellBarRow, { here, onOpen: () => {} }));

  it("names the app only in the command bar's own scope chip", () => {
    const html = row({ label: "My Tasks", icon: "CheckSquare" });
    expect(html.match(/My Tasks/g) ?? []).toHaveLength(1);
    expect(html).toContain("in My Tasks");
    // The chip is inside the command bar's button, not beside it.
    const button = html.slice(html.indexOf("<button"), html.indexOf("</button>"));
    expect(button).toContain("in My Tasks");
    // No heading, no app icon, no slot.
    expect(html).not.toMatch(/<h1|lucide-check-square|empty:hidden/);
  });

  it("draws the same bar in every app, but for that chip", () => {
    const a = row({ label: "My Tasks", icon: "CheckSquare" }).replace("in My Tasks", "in ·");
    const b = row({ label: "Projects", icon: "FolderKanban" }).replace("in Projects", "in ·");
    expect(a).toBe(b);
  });

  it("holds three zones and nothing an app could fill", () => {
    const html = row(null);
    // The header's children: a spacer, the command bar, the end zone.
    const inner = html.replace(/^<header[^>]*>/, "").replace(/<\/header>$/, "");
    expect(inner.startsWith('<div aria-hidden="true" class="min-w-0 flex-1 basis-0"></div><button')).toBe(true);
    expect(inner.endsWith('<div class="flex min-w-0 flex-1 basis-0 items-center justify-end gap-1"></div>')).toBe(true);
  });
});
