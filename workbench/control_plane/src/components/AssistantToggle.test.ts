/**
 * The assistant button sits at the right end of the app's TOP BAR, in My Tasks
 * and in Projects, and both draw it with ONE component (owner ask, 2026-09-24).
 *
 * Until then My Tasks drew a hand-rolled `<button>` in its top bar, and
 * Projects drew a `Button` in the project header one row lower. Two looks and
 * two places for one control, which is what this file stops from coming back.
 *
 * `vitest.config.ts` runs in the `node` environment, with no DOM, so this is a
 * source test: it cuts the top bar out of each page and looks inside it. The
 * top bar is the slim `h-10` row the desktop layout opens with, and it ends
 * where the pane row starts.
 *
 * Rooted at `src/` via `import.meta.url`, never at the process cwd.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

const TOP_BAR_OPEN = 'className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-card px-2"';

/** The desktop top bar: from its opening `div` to the end marker given. */
function topBar(source: string, end: string): string {
  // The desktop layout is the LAST `h-10` bar with this exact class string.
  // Projects' phone layout opens with an `h-10` bar too, but a different one.
  const start = source.lastIndexOf(TOP_BAR_OPEN);
  expect(start, "the desktop top bar's class string moved").toBeGreaterThan(-1);
  const stop = source.indexOf(end, start);
  expect(stop, `the top bar's end marker "${end}" moved`).toBeGreaterThan(start);
  return source.slice(start, stop);
}

const APPS = [
  { name: "Projects", file: "app/projects/page.tsx", end: "<SidePanelFitContext.Provider" },
  { name: "My Tasks", file: "app/tasks/page.tsx", end: '<div className="flex min-h-0 flex-1 overflow-hidden">' },
];

describe("the assistant button lives in the top bar", () => {
  for (const app of APPS) {
    it(`${app.name} mounts AssistantToggle once, inside its top bar`, () => {
      const source = read(app.file);
      expect(source).toMatch(/import AssistantToggle from "@\/components\/AssistantToggle";/);
      expect(source.match(/<AssistantToggle\b/g) ?? []).toHaveLength(1);
      expect(topBar(source, app.end)).toMatch(/<AssistantToggle\b/);
    });

    it(`${app.name} draws no second assistant button of its own`, () => {
      const source = read(app.file);
      // A hand-rolled or `Button`-drawn "Assistant" label outside the shared
      // component is the old shape coming back.
      expect(source).not.toMatch(/>\s*Assistant\s*<\/(button|Button)>/);
      expect(source).not.toMatch(/icon="Sparkles"/);
    });
  }

  it("Projects' project header does not carry it — the place it moved from", () => {
    const source = read("app/projects/page.tsx");
    const start = source.indexOf('<header className="shrink-0 border-b border-border">');
    const stop = source.indexOf("</header>", start);
    expect(start).toBeGreaterThan(-1);
    expect(stop).toBeGreaterThan(start);
    const header = source.slice(start, stop);
    expect(header).not.toMatch(/<AssistantToggle\b/);
    expect(header).not.toMatch(/Sparkles/);
  });

  it("the button is the shared primitive, not a hand-rolled control", () => {
    const source = read("components/AssistantToggle.tsx");
    expect(source).toMatch(/import Button from "@\/components\/ui\/Button";/);
    expect(source).not.toMatch(/<button\b/);
  });

  it("Projects wires the button through assistantButton, with the slot, behind the chat flag", () => {
    const source = read("app/projects/page.tsx");
    const bar = topBar(source, "<SidePanelFitContext.Provider");
    // The flag gate: the button exists only where the chat is live.
    expect(bar).toMatch(/\{CHAT_LIVE \? \(\s*<AssistantToggle\b/);
    // Every decision comes from the pure function the chatDock tests cover.
    expect(bar).toMatch(/open=\{assistant\.pressed\}/);
    expect(bar).toMatch(/title=\{assistant\.title\}/);
    expect(bar).toMatch(/const \{ press \} = assistant;/);
    // The slot reaches it, or the button is unpressed over the slot and a
    // press there changes nothing on screen.
    expect(source).toMatch(
      /const assistant = assistantButton\(\{\s*state: dockState,\s*wide: dockWide,\s*slotOpen: app === "ai-chat",\s*\}\);/,
    );
  });
});
