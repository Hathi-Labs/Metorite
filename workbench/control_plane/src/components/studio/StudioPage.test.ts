/**
 * STU-D11b fence: the Studio page layout.
 *
 * The vitest env has no DOM and takes `*.test.ts` only. So the page draws from props with
 * `renderToStaticMarkup`, as `StudioBoard.test.ts` does.
 */
import { readFileSync } from "node:fs";
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/page-preview",
  useSearchParams: () => new URLSearchParams(),
}));

import StudioPage from "@/components/studio/StudioPage";
import type { PageDef, ViewDef } from "@/lib/studio/manifest";
import { EMPTY_PAGE_STATE, type PageState } from "@/lib/studio/pageState";

const SRC = readFileSync(new URL("./StudioPage.tsx", import.meta.url), "utf-8");

const VIEWS: ViewDef[] = [
  { id: "a-table", block: "table" },
  { id: "b-board", block: "board" },
  { id: "c-detail", block: "detail" },
  { id: "d-chart", block: "chart" },
];

function draw(
  pages: PageDef[] | undefined,
  state: Partial<PageState> = {},
  renderView: (id: string) => ReactNode = (id) => createElement("i", { "data-v": id }, id),
  views: ViewDef[] = VIEWS,
): string {
  return renderToStaticMarkup(createElement(StudioPage, {
    title: "Visits", manifest: { views, pages }, state: { ...EMPTY_PAGE_STATE, ...state },
    onTab: () => {}, onCloseSide: () => {}, renderView,
  }));
}

describe("the grid", () => {
  it("uses one column in a narrow scroller, and cols only as the scroller widens", () => {
    for (const [cols, cls] of [[1, "grid-cols-1"], [2, "@xl:grid-cols-2"], [3, "@3xl:grid-cols-3"], [4, "@5xl:grid-cols-4"]] as const) {
      const html = draw([{ id: "p", cols, cells: [{ view: "a-table" }] }]);
      expect(html).toContain("@container");
      expect(html).toContain(cls);
      // The one-column rule: no grid-cols class takes effect without a width condition.
      expect(html).toContain("grid-cols-1");
      expect(html.match(/(?<![@\w:-])grid-cols-[2-9]/g)).toBeNull();
    }
  });

  it("gives each cell its span, and a span of 1 adds no class", () => {
    const html = draw([{ id: "p", cols: 3, cells: [{ view: "a-table", span: 2 }, { view: "b-board" }] }]);
    expect(html.match(/@xl:col-span-2/g)).toHaveLength(1);
    expect(html).not.toContain("col-span-1");
  });

  it("calls renderView once for each cell, in order", () => {
    const seen: string[] = [];
    draw([{ id: "p", cells: [{ view: "b-board" }, { view: "a-table" }, { view: "d-chart" }] }], {}, (id) => {
      seen.push(id);
      return createElement("i", null, id);
    });
    expect(seen).toEqual(["b-board", "a-table", "d-chart"]);
  });

  it("builds no class from a manifest value", () => {
    const html = draw([{ id: "p", cols: 99, cells: [{ view: "a-table" }] }]);
    expect(html).not.toContain("99");
  });

  it("draws the default page when the manifest has no pages", () => {
    const html = draw(undefined);
    for (const v of VIEWS) expect(html).toContain(`data-v="${v.id}"`);
    expect(html).toContain("grid-cols-1");
  });
});

describe("the tabs", () => {
  const PAGES: PageDef[] = [{
    id: "p", tabs: [
      { id: "list", title: "List", cells: [{ view: "a-table" }] },
      { id: "plan", title: "Plan", cells: [{ view: "b-board" }] },
    ],
  }];

  it("draws the underline tabs and the cells of the first tab", () => {
    const html = draw(PAGES);
    expect(html).toContain('role="tablist"');
    expect(html).toMatch(/aria-selected="true"[^>]*>List/);
    expect(html).toContain('data-v="a-table"');
    expect(html).not.toContain('data-v="b-board"');
  });

  it("draws the cells of the tab in the state", () => {
    const html = draw(PAGES, { tab: "plan" });
    expect(html).toMatch(/aria-selected="true"[^>]*>Plan/);
    expect(html).toContain('data-v="b-board"');
    expect(html).not.toContain('data-v="a-table"');
  });

  it("falls back to the first tab for an unknown tab", () => {
    expect(draw(PAGES, { tab: "ghost" })).toContain('data-v="a-table"');
  });

  it("draws no tab bar for a page with cells", () => {
    expect(draw([{ id: "p", cells: [{ view: "a-table" }] }])).not.toContain('role="tablist"');
  });
});

describe("the side view", () => {
  const PAGES: PageDef[] = [{ id: "p", cells: [{ view: "a-table" }], side: { view: "c-detail" } }];

  it("draws a 380 px column when a record is selected", () => {
    const html = draw(PAGES, { selected: "v1" });
    expect(html).toContain("<aside");
    expect(html).toContain("w-[380px]");
    expect(html).toContain("lg:block");
    expect(html).toContain('data-v="c-detail"');
  });

  it("draws no column when nothing is selected", () => {
    const html = draw(PAGES);
    expect(html).not.toContain("<aside");
    expect(html).not.toContain('data-v="c-detail"');
  });

  it("draws no column for a page with no side view", () => {
    expect(draw([{ id: "p", cells: [{ view: "a-table" }] }], { selected: "v1" })).not.toContain("<aside");
  });

  it("opens in a sheet Modal below lg", () => {
    expect(SRC).toMatch(/<Modal[^>]*placement="sheet"/);
    expect(SRC).toContain("max-width: 1023.98px");
  });
});

describe("a view that has no block, and a view that A2 refused", () => {
  it("shows the not-built card when renderView returns null", () => {
    const html = draw([{ id: "p", cells: [{ view: "d-chart" }, { view: "a-table" }] }], {},
      (id) => (id === "d-chart" ? null : createElement("i", { "data-v": id })));
    expect(html).toContain("This block is not built yet");
    expect(html).toContain('data-not-built="d-chart"');
    expect(html).toContain('data-v="a-table"');
  });

  it("drops a cell whose view the manifest does not declare, and never calls renderView for it", () => {
    const seen: string[] = [];
    const html = draw([{ id: "p", cells: [{ view: "ghost" }, { view: "a-table" }] }], {}, (id) => {
      seen.push(id);
      return createElement("i", { "data-v": id });
    });
    expect(seen).toEqual(["a-table"]);
    expect(html).not.toContain("ghost");
  });

  it("says that a page has no views when every cell was dropped", () => {
    expect(draw([{ id: "p", cells: [{ view: "ghost" }] }])).toContain("This page has no views.");
  });
});

describe("the shell stays the shell's", () => {
  it("has one h1 from AppTopBar, and no palette, bell, rail or key listener", () => {
    const html = draw([{ id: "p", cells: [{ view: "a-table" }] }]);
    expect(html.match(/<h1/g)).toHaveLength(1);
    expect(SRC).not.toMatch(/NotificationBell|CommandPalette|AgentChat|AssistantDock|keydown|metaKey/);
  });

  it("has no branch for a block, so the Board keeps the phone layout of D3", () => {
    expect(SRC.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/board|Board/);
  });

  it("reserves no space for the Ask bar", () => {
    expect(SRC.replace(/\/\*[\s\S]*?\*\//g, "")).not.toContain("pb-askbar");
  });
});
