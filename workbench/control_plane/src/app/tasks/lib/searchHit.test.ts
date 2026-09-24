/**
 * ⌘K is search in both task apps, through ONE palette (2026-09-24).
 *
 * Until then My Tasks bound ⌘K to capture and Projects bound it to search.
 * Now My Tasks mounts the Projects `SearchPalette` with no commands, and
 * capture stays on `C`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { COMMANDS, paletteCommands } from "@/app/projects/lib/commands";
import { PANEL_MODES } from "@/app/projects/lib/panelMode";

import { hitTarget, searchAllowed } from "./searchHit";

const SRC = fileURLToPath(new URL("../../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

describe("where a hit opens", () => {
  it("a task My Tasks holds opens here", () => {
    expect(hitTarget("t1", [{ id: "t0" }, { id: "t1" }])).toEqual({ kind: "here", id: "t1" });
  });

  it("any other task opens in Projects, at the board's deep link", () => {
    expect(hitTarget("a b", [{ id: "t0" }])).toEqual({
      kind: "projects",
      href: "/projects?task=a%20b",
    });
  });
});

describe("⌘K does not open search over another overlay", () => {
  const closed = {
    quickCaptureOpen: false,
    clarifyModalOpen: false,
    focusedItemId: null,
    settingsModalOpen: false,
    reclarifyItemId: null,
    pendingDeleteIds: null,
    scheduleItemId: null,
    eliminateItemId: null,
    delegateItemId: null,
  };

  it("opens when nothing is up", () => {
    expect(searchAllowed(closed, false)).toBe(true);
    expect(searchAllowed({ ...closed, pendingDeleteIds: [] }, false)).toBe(true);
  });

  it.each([
    ["the focus view", { focusedItemId: "t1" }],
    ["settings", { settingsModalOpen: true }],
    ["re-clarify", { reclarifyItemId: "t1" }],
    ["capture", { quickCaptureOpen: true }],
    ["clarify", { clarifyModalOpen: true }],
    ["a delete prompt", { pendingDeleteIds: ["t1"] }],
    ["schedule", { scheduleItemId: "t1" }],
    ["eliminate", { eliminateItemId: "t1" }],
    ["delegate", { delegateItemId: "t1" }],
  ])("stays shut while %s is open", (_what, over) => {
    expect(searchAllowed({ ...closed, ...over }, false)).toBe(false);
  });

  it("stays shut while the page's maximised view is up", () => {
    expect(searchAllowed(closed, true)).toBe(false);
  });

  it("the page asks it, with the live store and the maximised view", () => {
    expect(read("app/tasks/page.tsx")).toMatch(
      /if \(searchAllowed\(useTaskStore\.getState\(\), maximisedId !== null\)\) \{\s*setSearching\(true\);/,
    );
  });
});

describe("the palette with no context is task search only", () => {
  it("offers no command, whatever is typed", () => {
    expect(paletteCommands(undefined, "")).toEqual([]);
    expect(paletteCommands(undefined, "board")).toEqual([]);
  });

  it("offers the registry when Projects gives it a context", () => {
    const ctx = {
      mode: "board" as const,
      hasProject: true,
      isRoot: false,
      filtered: false,
      panelOpen: false,
      panelMode: PANEL_MODES[0],
      canToggleRail: true,
    };
    expect(paletteCommands(ctx, "").length).toBeGreaterThan(0);
    expect(COMMANDS.length).toBeGreaterThan(0);
  });
});

describe("My Tasks binds ⌘K to search", () => {
  const page = () => read("app/tasks/page.tsx");

  it("⌘K opens the search palette, not capture", () => {
    const src = page();
    expect(src).toMatch(
      /if \(isOpenShortcut\(e\)\) \{\s*e\.preventDefault\(\);\s*openSearch\(\);\s*return;\s*\}/,
    );
    // The old binding, gone.
    expect(src).not.toMatch(/e\.key === "k"/);
  });

  it("search opens through ONE guard, from the key and from the top bar", () => {
    const src = page();
    // The guard: no palette over another overlay (`searchAllowed`).
    expect(src).toMatch(
      /const openSearch = useCallback\(\(\) => \{\s*if \(searchAllowed\(useTaskStore\.getState\(\), maximisedId !== null\)\) \{\s*setSearching\(true\);\s*\}\s*\}, \[maximisedId\]\);/,
    );
    // Nothing else opens the palette around the guard.
    expect(src.match(/setSearching\(true\)/g) ?? []).toHaveLength(1);
    // The top bar's Search button is the second way in, through the guard.
    expect(src).toMatch(/<AppSearchButton onOpen=\{openSearch\} \/>/);
  });

  it("capture stays on C", () => {
    expect(page()).toMatch(/\(e\.key === "c" \|\| e\.key === "C"\)\s*\)\s*\{\s*e\.preventDefault\(\);\s*openQuickCapture\("single"\);/);
  });

  it("mounts the ONE palette, with no commands, and opens a hit through hitTarget", () => {
    const src = page();
    expect(src).toMatch(/import \{ SearchPalette \} from "\.\.\/projects\/components\/SearchPalette";/);
    expect(src).toMatch(
      /<SearchPalette\s+open=\{searching\}\s+onClose=\{\(\) => setSearching\(false\)\}\s+onOpenTask=\{openHit\}\s+\/>/,
    );
    expect(src).toMatch(/hitTarget\(id, useTaskStore\.getState\(\)\.items\)/);
    // Both layouts, phone and desktop, carry it.
    expect(src.match(/\{search\}/g) ?? []).toHaveLength(2);
  });

  it("the shortcut legend says ⌘K searches", () => {
    const inbox = read("app/tasks/components/InboxView.tsx");
    expect(inbox).toMatch(/<Sc k="⌘K">search<\/Sc>/);
    expect(read("app/tasks/components/QuickCapture.tsx")).not.toMatch(/⌘K/);
  });

  it("there is one search palette in the tree", () => {
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.tsx$/.test(entry)) files.push(relative(SRC, full).split(sep).join("/"));
      }
    };
    walk(SRC);
    const palettes = files.filter((f) => /function\s+\w*SearchPalette\b/.test(read(f)));
    expect(palettes).toEqual(["app/projects/components/SearchPalette.tsx"]);
  });
});
