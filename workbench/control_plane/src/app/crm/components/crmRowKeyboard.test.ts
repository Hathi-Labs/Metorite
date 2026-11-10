// A keyboard opens a record from every CRM list (DESIGN_SYSTEM §9).
// The row opens on a click, and Tab cannot reach a <tr>. The first cell of each
// row is a real button, so Tab reaches it and Enter opens the record.
// The runner has no DOM, so the list is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MODULE_COLUMNS } from "../lib/modules";
import RecordList from "./RecordList";

const noop = () => undefined;

const base = {
  total: 2,
  loading: false,
  sort: null,
  direction: "asc" as const,
  onSort: noop,
  onOpen: noop,
};

/** The cells of each body row, as markup. */
function firstCells(html: string): string[] {
  const body = html.slice(html.indexOf("<tbody>"));
  return [...body.matchAll(/<tr[^>]*><td[^>]*>(.*?)<\/td>/g)].map((m) => m[1]);
}

describe("a record opens from the keyboard", () => {
  it("puts a button in the first cell of each row of an entity list", () => {
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        ...base,
        entity: "deals",
        rows: [
          { id: "1", name: "Deal one" },
          { id: "2", name: "Deal two" },
        ],
      }),
    );
    const cells = firstCells(html);
    expect(cells).toHaveLength(2);
    for (const [i, name] of ["Deal one", "Deal two"].entries()) {
      expect(cells[i]).toMatch(/^<button type="button"[^>]*class="[^"]*cc-link[^"]*"/);
      expect(cells[i]).toContain(name);
    }
  });

  it("does the same in a generic module list", () => {
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        ...base,
        entity: "module:Invoices",
        columns: MODULE_COLUMNS,
        sortKeys: [],
        rows: [
          { id: "a", name: "INV-1" },
          { id: "b", name: "INV-2" },
        ],
      }),
    );
    const cells = firstCells(html);
    expect(cells).toHaveLength(2);
    expect(cells.every((c) => c.startsWith('<button type="button"'))).toBe(true);
  });

  it("keeps the other cells as text", () => {
    const html = renderToStaticMarkup(
      createElement(RecordList, { ...base, entity: "deals", rows: [{ id: "1", name: "Deal one" }] }),
    );
    const body = html.slice(html.indexOf("<tbody>"));
    expect(body.match(/<button/g)).toHaveLength(1);
  });
});
