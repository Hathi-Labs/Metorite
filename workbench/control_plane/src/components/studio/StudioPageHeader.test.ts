/**
 * STU-AF7 fence: the header and the sections of a Studio page, drawn from props with
 * `renderToStaticMarkup` (the env has no DOM).
 */
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/package-preview",
  useSearchParams: () => new URLSearchParams(),
}));

import StudioPage from "@/components/studio/StudioPage";
import type { PageDef, ViewDef } from "@/lib/studio/manifest";
import { EMPTY_PAGE_STATE } from "@/lib/studio/pageState";

const VIEWS: ViewDef[] = [
  { id: "a-table", block: "table", title: "Visits" },
  { id: "b-board", block: "board", title: "Board" },
];

function draw(page: PageDef, extra: Record<string, unknown> = {}, renderView?: (id: string) => ReactNode): string {
  return renderToStaticMarkup(createElement(StudioPage, {
    title: "Field visits", manifest: { views: VIEWS, pages: [page] }, state: EMPTY_PAGE_STATE,
    onTab: () => {}, onCloseSide: () => {}, foldScope: "field-visits",
    renderView: renderView ?? ((id: string) => createElement("i", { "data-v": id }, id)), ...extra,
  }));
}

describe("the page header", () => {
  it("draws no header for a page with none", () => {
    const html = draw({ id: "p", cells: [{ view: "a-table" }] });
    expect(html).not.toContain("<header");
  });

  it("draws the sentence under the page title, through PageHeader", () => {
    const html = draw({ id: "p", title: "This week", header: { text: "Every visit planned for this week." }, cells: [{ view: "a-table" }] });
    expect(html).toContain("<header");
    expect(html).toContain("<h2");
    expect(html).toContain("This week");
    expect(html).toContain("Every visit planned for this week.");
  });

  it("draws the count that the app gives, beside the title", () => {
    const html = draw(
      { id: "p", title: "This week", header: { text: "Hi.", count: { object: "visit", label: "visits" } }, cells: [{ view: "a-table" }] },
      { renderCount: (c: { object: string; label: string | null }) => createElement("b", null, `12 ${c.label}`) },
    );
    expect(html).toContain("<b>12 visits</b>");
  });

  it("draws no count when the app gives no counter, and the text stays", () => {
    const html = draw({ id: "p", header: { text: "Hi.", count: { object: "visit" } }, cells: [{ view: "a-table" }] });
    expect(html).toContain("Hi.");
    expect(html).not.toContain("<b>");
  });

  it("draws the sentence as text, never as markup", () => {
    const html = draw({ id: "p", header: { text: "<img src=x onerror=1>" }, cells: [{ view: "a-table" }] });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("the sections", () => {
  const page: PageDef = {
    id: "p", cols: 2,
    sections: [
      { id: "open", title: "Open visits", cells: [{ view: "a-table" }] },
      { id: "board", title: "The board", cells: [{ view: "b-board" }] },
      { id: "extra", title: "Unbuilt", cells: [{ view: "a-table" }] },
    ],
  };

  it("draws each section with its title, folded through the shared Collapsible", () => {
    const html = draw(page);
    for (const t of ["Open visits", "The board", "Unbuilt"]) expect(html).toContain(t);
    expect(html.match(/aria-expanded="true"/g)).toHaveLength(3);
    expect(html).toContain("data-page-sections");
    expect(html.match(/data-page-grid/g)).toHaveLength(3);
  });

  it("keeps the order of the sections", () => {
    const html = draw(page);
    expect(html.indexOf("Open visits")).toBeLessThan(html.indexOf("The board"));
    expect(html.indexOf("The board")).toBeLessThan(html.indexOf("Unbuilt"));
  });

  it("shows the not-built card for a cell that no block draws", () => {
    const html = draw(page, {}, (id) => (id === "b-board" ? null : createElement("i", null, id)));
    expect(html).toContain("data-not-built=\"b-board\"");
  });

  it("drops a section cell of an unknown view and still draws the others", () => {
    const html = draw({ id: "p", sections: [{ id: "s", title: "Mixed", cells: [{ view: "ghost" }, { view: "a-table" }] }] });
    expect(html).toContain("Mixed");
    expect(html).toContain('data-v="a-table"');
    expect(html).not.toContain("ghost");
  });

  it("a page of cells draws no section and no fold control", () => {
    const html = draw({ id: "p", cells: [{ view: "a-table" }] });
    expect(html).not.toContain("data-page-sections");
    expect(html).not.toContain("aria-expanded");
  });
});
