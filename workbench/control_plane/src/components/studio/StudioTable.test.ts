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

import GenerativeUINode, { BoundStatus } from "@/components/GenerativeUINode";
import StudioTable, { updateOf } from "@/components/studio/StudioTable";
import { clearAll, put } from "@/lib/dataCache";
import { tableBlock } from "@/lib/studio/blocks";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type StudioDataDoor, sourceKey } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";

const door: StudioDataDoor = { scope: "t:live", read: async () => [], run: vi.fn(async () => ({ ok: true })) };
const FIELDS: FieldSpec[] = [
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

describe("fix round 1: the plain grid, the read error, the edit mark, the refusal line", () => {
  const PKG_FIELDS = FIELDS.filter((f) => f.key !== "total");
  const bound = (editable: boolean) => {
    const src = { action: "object:visit.list" };
    const d: StudioDataDoor = { scope: "t:fix1", read: async () => [], run: vi.fn(async () => ({ ok: true })) };
    put(sourceKey(d, src), ROWS);
    return renderToStaticMarkup(createElement(
      StudioDataContext.Provider, { value: d },
      createElement(GenerativeUINode, { spec: tableBlock(PKG_FIELDS, src, "Visits", editable ? { editable: true } : undefined) }),
    ));
  };
  it("a bound dataGrid without editable draws with no StudioTable wrapper and no edit button", () => {
    clearAll();
    const html = bound(false);
    expect(html).toContain("Filter rows");
    expect(html).not.toContain('aria-live="polite"');
    expect(html).not.toContain("data-cell-edit");
  });
  it("the same grid with editable draws the wrapper", () => {
    clearAll();
    const html = bound(true);
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain("data-cell-edit");
  });
  it("a read error shows the sentence of the door once, with no retry for a refusal", () => {
    const html = renderToStaticMarkup(createElement(BoundStatus, { view: "error", onRetry: () => {}, error: "That record is gone. Reload the list." }));
    expect(html).toContain("That record is gone. Reload the list.");
    expect(html).not.toContain("Could not load this list.");
    expect(html).not.toContain("Try again");
    expect((html.match(/role="alert"/g) ?? []).length).toBe(1);
  });
  it("a wait or a network fault shows its sentence and a Retry", () => {
    const html = renderToStaticMarkup(createElement(BoundStatus, { view: "error", onRetry: () => {}, error: "This app is busy now. Try again in 1 second." }));
    expect(html).toContain("This app is busy now.");
    expect(html).toContain(">Try again<");
  });
  it("a fault with no sentence keeps the old words and a Retry", () => {
    const html = renderToStaticMarkup(createElement(BoundStatus, { view: "error", onRetry: () => {} }));
    expect(html).toContain("Could not load this list.");
    expect(html).toContain(">Try again<");
  });
  it("an editable cell wears a pencil mark that needs no hover", () => {
    const html = draw({ title: "Visits", fields: FIELDS, filterable: true, editable: true, computed: ["total"] });
    expect(html).toContain("data-edit-mark");
    expect((html.match(/data-edit-mark/g) ?? []).length).toBe(html.match(/data-cell-edit/g)?.length);
  });
  it("keeps room for the refusal line, so the grid does not jump", () => {
    const html = draw({ title: "Visits", fields: FIELDS, filterable: true, editable: true });
    expect(html).toContain('data-refusal-slot');
  });
});
