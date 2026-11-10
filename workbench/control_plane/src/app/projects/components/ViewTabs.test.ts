/**
 * WS-64 OP-12 (D-OP-17): the view tab row, and the View sheet without its tabs.
 * The fold arithmetic is `lib/viewTabs.test.ts`. This holds the markup.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ModeSwitch } from "@/components/ModeSwitch";

import type { ViewRow } from "../lib/api";
import { VIEW_MODES } from "../lib/commands";
import { EMPTY_FILTERS, NO_LANES } from "../lib/grouping";
import { DEFAULT_SHOWN } from "../lib/shownFields";
import { ViewSheetBody } from "./ViewSheet";
import { ViewTabs } from "./ViewTabs";

const noop = () => {};
const views = [
  { id: "v1", project_id: "p", name: "My open bugs", view_type: "list", config: {} },
  { id: "v2", project_id: "p", name: "Sprint", view_type: "board", config: {} },
] as ViewRow[];

const tabs = (activeViewId: string | null, canvas: "board" | null = "board") =>
  renderToStaticMarkup(
    createElement(ViewTabs, {
      canvas,
      onCanvas: noop,
      views,
      activeViewId,
      onView: noop,
      onAddView: noop,
      onDeleteView: noop,
      canAddView: true,
    })
  );

describe("the view tab row", () => {
  const html = tabs("v2");

  it("draws the six canvases first, then the saved views, then + View", () => {
    const order = [...VIEW_MODES.map((m) => m.id[0].toUpperCase() + m.id.slice(1)), "My open bugs", "Sprint", "View"];
    let at = -1;
    for (const label of order) {
      const next = html.indexOf(`${label}</button>`, at + 1);
      expect(next, label).toBeGreaterThan(at);
      at = next;
    }
  });

  it("puts the ON pair on the canvas and on the saved view, through ModeSwitch", () => {
    expect(html.match(/aria-pressed="true"/g)?.length).toBe(2);
    expect(html).toContain("bg-primary/10 text-primary");
  });

  it("names the key of each canvas in a tooltip", () => {
    for (const m of VIEW_MODES) expect(html).toContain(`(${m.key})`);
  });

  it("selects no canvas while the lens is on", () => {
    expect(tabs(null, null)).not.toContain('aria-pressed="true"');
  });

  it("has no sideways scroll", () => {
    expect(html).not.toMatch(/overflow-x-auto|overflow-x-scroll|overflow-auto/);
  });

  it("offers a way to delete the view on screen", () => {
    expect(html).toContain("Delete view Sprint");
    expect(tabs(null)).not.toContain("Delete view");
  });
});

describe("ModeSwitch icon-only", () => {
  const html = renderToStaticMarkup(
    createElement(ModeSwitch<"list" | "board">, {
      modes: [
        { id: "list", icon: "List", title: "List (l)" },
        { id: "board", icon: "Kanban", title: "Board (b)" },
      ],
      mode: "board",
      onPick: noop,
      iconOnly: true,
    })
  );

  it("keeps the name and the tooltip when the label goes", () => {
    expect(html).toContain('aria-label="Board"');
    expect(html).toContain('title="Board (b)"');
    expect(html).not.toMatch(/>Board<\/button>/);
    expect(html).toContain('aria-pressed="true"');
  });
});

describe("the View sheet", () => {
  const props = {
    filters: EMPTY_FILTERS,
    mode: "board" as const,
    modes: VIEW_MODES.map((m) => ({ id: m.id, icon: m.icon })),
    onMode: noop,
    spansProjects: true,
    groupBy: "status" as const,
    onGroupBy: noop,
    lanes: NO_LANES,
    onSubGroupBy: noop,
    subtasks: "inline" as never,
    onSubtasks: noop,
    shownFields: [...DEFAULT_SHOWN],
    onShownFields: noop,
    fields: [],
    views,
    activeViewId: null,
    onApplyView: noop,
    onSaveView: noop,
    onDeleteView: noop,
    onUpdateView: noop,
    canSave: true,
    onExport: async () => {},
  };

  it("keeps the canvas strip and the saved views on a phone", () => {
    const phone = renderToStaticMarkup(createElement(ViewSheetBody, props as never));
    expect(phone).toContain('aria-label="View mode"');
    expect(phone).toContain("Saved views");
    expect(phone).toContain("My open bugs");
  });

  it("drops both on a desktop and a tablet", () => {
    const desk = renderToStaticMarkup(createElement(ViewSheetBody, { ...props, tabs: false } as never));
    expect(desk).not.toContain('aria-label="View mode"');
    expect(desk).not.toContain("Saved views");
    expect(desk).not.toContain("My open bugs");
    expect(desk).toContain("Export");
  });

  it("the page mounts the tab row only with the compact chrome, and the sheet follows the phone flag", () => {
    const src = (rel: string) => readFileSync(join(fileURLToPath(new URL("..", import.meta.url)), rel), "utf8");
    expect(src("page.tsx")).toMatch(/compactChrome \? \(\s*\/\/[^\n]*\n[^\n]*\n\s*<ViewTabs/);
    expect(src("components/ViewSheet.tsx")).toContain("tabs={phone}");
  });
});
