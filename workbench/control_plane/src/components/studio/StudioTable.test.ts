/**
 * STU-D2b-1 fence: the Table draws an edit button on the cells that can edit.
 *
 * The store (`inlineEdit.ts`) holds the write rules. This test holds the view:
 * a scalar cell gets a button, a cell of a computed or long-text field gets
 * none, a tick draws as a checkbox, and a Table that is not `editable` draws
 * as before.
 *
 * Mutation 1: draw the button for every column. "no button on a computed field"
 * turns red.
 * Mutation 2: link a row by `openBase + id` when `openBase` is empty (`DataGrid`).
 * "a row with a UUID id" turns red: the live preview showed a bad relative link.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/package-preview",
}));

import StudioTable, { updateOf } from "@/components/studio/StudioTable";
import type { StudioDataDoor } from "@/lib/studio/dataDoor";

const door: StudioDataDoor = { scope: "t:live", read: async () => [], run: vi.fn(async () => ({ ok: true })) };
const FIELDS = [
  { key: "title", type: "text", label: "Title", required: true },
  { key: "qty", type: "number", label: "Pallets" },
  { key: "total", type: "number", label: "Total" },
  { key: "notes", type: "longText", label: "Notes" },
  { key: "done", type: "checkbox", label: "Done" },
];
const ROWS = [{ id: "r1", title: "A", qty: 2, total: 4, notes: "x", done: true }];
const SOURCE = { action: "object:visit.list" };

const draw = (data: Record<string, unknown>) =>
  renderToStaticMarkup(createElement(StudioTable, { data, rows: ROWS, door, source: SOURCE }));

describe("the Table with inline edit", () => {
  const data = { title: "Visits", fields: FIELDS, filterable: true, editable: true, computed: ["total"] };
  it("draws an edit button on a scalar cell", () => {
    const html = draw(data);
    expect(html).toContain('aria-label="Edit Title"');
    expect(html).toContain('aria-label="Edit Pallets"');
  });
  it("draws no button on a computed field", () => {
    expect(draw(data)).not.toContain('aria-label="Edit Total"');
  });
  it("draws no button on a long text", () => {
    expect(draw(data)).not.toContain('aria-label="Edit Notes"');
  });
  it("draws a tick as a checkbox", () => {
    expect(draw(data)).toContain('type="checkbox"');
  });
  it("draws no edit button when the node is not editable", () => {
    const html = draw({ ...data, editable: false });
    expect(html).not.toContain("data-cell-edit");
  });
  it("sends nothing on draw", () => {
    draw(data);
    expect(door.run).not.toHaveBeenCalled();
  });
});

describe("updateOf", () => {
  it("pairs a list with its update, and nothing else", () => {
    expect(updateOf({ action: "object:visit.list" })).toBe("object:visit.update");
    expect(updateOf({ action: "object:visit.get" })).toBeNull();
    expect(updateOf({ action: "find" })).toBeNull();
    expect(updateOf(null)).toBeNull();
  });
});

describe("a row with a UUID id", () => {
  const ID = "123e4567-e89b-12d3-a456-426614174000";
  const data = { title: "Visits", fields: FIELDS, filterable: true, editable: true };
  it("draws the title with no link, because an empty openBase means no link", () => {
    const html = renderToStaticMarkup(createElement(StudioTable, {
      data, rows: [{ id: ID, title: "A", qty: 1 }], door, source: SOURCE,
    }));
    expect(html).not.toContain(`href="${ID}"`);
    expect(html).not.toContain("<a ");
    expect(html).toContain('aria-label="Edit Title"');
  });
});
