/**
 * STU-D17a: the paste of rows into the sheet. Quoted cells, CRLF, a trailing line break, a header
 * row, a bad cell, the cap, and 20 rows that make 20 `{data}` lines.
 */
import { describe, expect, it } from "vitest";

import { fromStored, buildSave, saveCount, sheetColumns, valueOf } from "./sheetModel";
import { pasteInto, parseTsv } from "./sheetPaste";
import type { FieldSpec } from "./fieldCheck";

const specs: FieldSpec[] = [
  { key: "name", type: "text", label: "Name" },
  { key: "pct", type: "number", label: "Pct" },
  { key: "note", type: "text", label: "Note" },
  { key: "formulation", type: "relation", to: "formulation" },
];
const cols = sheetColumns(specs, "formulation", new Set(), true);
const none = { uid: null, key: null };

describe("parseTsv", () => {
  it("reads tabs and line breaks", () => {
    expect(parseTsv("a\tb\nc\td")).toEqual([["a", "b"], ["c", "d"]]);
  });

  it("reads CRLF and drops the trailing line break", () => {
    expect(parseTsv("a\tb\r\nc\td\r\n")).toEqual([["a", "b"], ["c", "d"]]);
    expect(parseTsv("a\n")).toEqual([["a"]]);
    expect(parseTsv("")).toEqual([]);
  });

  it("reads a quoted cell with a tab, a doubled quote and a line break", () => {
    expect(parseTsv("\"x\ty\"\t\"say \"\"hi\"\"\"\n\"line 1\r\nline 2\"\tz\r\n"))
      .toEqual([["x\ty", "say \"hi\""], ["line 1\r\nline 2", "z"]]);
  });

  it("keeps an empty cell and an empty last cell", () => {
    expect(parseTsv("a\t\tc\nd\t\n")).toEqual([["a", "", "c"], ["d", ""]]);
  });
});

describe("pasteInto", () => {
  it("makes a new row for each pasted row, with no focus", () => {
    const out = pasteInto(fromStored([]), cols, "Water\t60\nOil\t40\n", none);
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    expect(out.rows).toBe(2);
    expect(out.buf.rows.map((r) => [valueOf(r, "name"), valueOf(r, "pct")])).toEqual([["Water", 60], ["Oil", 40]]);
  });

  it("maps the columns from the focused column", () => {
    const out = pasteInto(fromStored([]), cols, "60\tnote a\n40\tnote b", { uid: null, key: "pct" });
    expect(out.ok).toBe(true);
    if (out.ok) expect(out.buf.rows.map((r) => [valueOf(r, "pct"), valueOf(r, "note")])).toEqual([[60, "note a"], [40, "note b"]]);
  });

  it("starts on the focused row, changes its cells and adds rows past the end", () => {
    const base = fromStored([{ id: "a1", name: "Water", pct: 60 }, { id: "a2", name: "Oil", pct: 40 }]);
    const out = pasteInto(base, cols, "50\n30\n20", { uid: base.rows[1].uid, key: "pct" });
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    expect(out.buf.rows).toHaveLength(4);
    expect(out.buf.rows[0].edits).toEqual({});
    expect(out.buf.rows[1].edits).toEqual({ pct: 50 });
    expect(out.buf.rows[2].id).toBeNull();
    expect(saveCount(out.buf)).toBe(3);
  });

  it("skips a header row that matches the column labels", () => {
    const out = pasteInto(fromStored([]), cols, "Name\tPct\r\nWater\t60\r\n", none);
    expect(out.ok && out.headerSkipped).toBe(true);
    if (out.ok) expect(out.buf.rows).toHaveLength(1);
    // A data row is not a header.
    const data = pasteInto(fromStored([]), cols, "Name\t60", none);
    expect(data.ok && data.headerSkipped).toBe(false);
  });

  it("refuses the whole paste on a bad cell, as 'Row 3, Pct: ...', and changes nothing", () => {
    const out = pasteInto(fromStored([]), cols, "Name\tPct\nA\t1\nB\tlots\nC\t3", none);
    expect(out.ok).toBe(false);
    if (!out.ok) expect(out.message).toMatch(/^Row 3, Pct: /);
  });

  it("refuses a row with more cells than columns from the focus", () => {
    const out = pasteInto(fromStored([]), cols, "a\t1\tn\textra", none);
    expect(out).toEqual({ ok: false, message: "Row 1 has 4 cells, but 3 columns can take them from here." });
  });

  it("refuses a paste that holds nothing", () => {
    expect(pasteInto(fromStored([]), cols, "\n\n", none).ok).toBe(false);
  });

  it("refuses a paste in a sheet with no editable column (a viewer)", () => {
    const viewer = sheetColumns(specs, "formulation", new Set(), false);
    expect(pasteInto(fromStored([]), viewer, "a\t1", none).ok).toBe(false);
  });

  it("makes 20 rows into 20 {data} lines of ONE body", () => {
    const text = Array.from({ length: 20 }, (_, i) => `item ${i + 1}\t5`).join("\r\n") + "\r\n";
    const out = pasteInto(fromStored([]), cols, text, none);
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    const body = buildSave(out.buf, { recordId: "r1", linesKey: "ingredients", canWrite: true });
    expect(body.ok).toBe(true);
    if (!body.ok) return;
    const lines = body.body.args.data.ingredients as { data: Record<string, unknown> }[];
    expect(lines).toHaveLength(20);
    expect(lines[0]).toEqual({ data: { name: "item 1", pct: 5 } });
  });

  it("refuses a paste over the cap of 49, new and changed rows together, with no chunking", () => {
    const rows = (n: number) => Array.from({ length: n }, (_, i) => `i${i}\t1`).join("\n");
    expect(pasteInto(fromStored([]), cols, rows(49), none).ok).toBe(true);
    const over = pasteInto(fromStored([]), cols, rows(50), none);
    expect(over.ok).toBe(false);
    if (!over.ok) expect(over.message).toMatch(/at most 49 lines.*nothing was pasted/);
    // Changed rows count too: 30 stored rows changed plus 20 new rows is 50.
    const stored = Array.from({ length: 30 }, (_, i) => ({ id: `s${i}`, name: `n${i}`, pct: 1 }));
    const base = fromStored(stored);
    const mixed = pasteInto(base, cols, Array.from({ length: 50 }, () => "x\t2").join("\n"), { uid: base.rows[0].uid, key: "name" });
    expect(mixed.ok).toBe(false);
  });
});
