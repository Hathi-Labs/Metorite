import { describe, expect, it } from "vitest";

import { colIndex, parseSharedStrings, readSheet, resolveTarget, sheetToMarkdown } from "./xlsx";
import { el } from "./xmlTree.testkit";

const c = (r: string, v: string, t?: string) => el("c", t ? { r, t } : { r }, el("v", {}, v));
const row = (...cells: ReturnType<typeof c>[]) => el("row", {}, ...cells);
const sheet = (...rows: ReturnType<typeof row>[]) => el("worksheet", {}, el("sheetData", {}, ...rows));

describe("xlsx helpers", () => {
  it("turns column letters into an index", () => {
    expect(["A", "B", "Z", "AA", "AB", "AD12"].map(colIndex)).toEqual([0, 1, 25, 26, 27, 29]);
    expect(colIndex("12")).toBe(-1);
  });

  it("reads shared strings with rich runs and skips phonetic runs", () => {
    const sst = el(
      "sst", {},
      el("si", {}, el("t", {}, "Plain")),
      el("si", {}, el("r", {}, el("t", {}, "Rich ")), el("r", {}, el("t", {}, "text")), el("rPh", {}, el("t", {}, "ignored"))),
    );
    expect(parseSharedStrings(sst)).toEqual(["Plain", "Rich text"]);
  });

  it("resolves a relationship target from the xl folder", () => {
    expect(resolveTarget("worksheets/sheet1.xml")).toBe("xl/worksheets/sheet1.xml");
    expect(resolveTarget("/xl/worksheets/sheet2.xml")).toBe("xl/worksheets/sheet2.xml");
    expect(resolveTarget("../xl/worksheets/s.xml")).toBe("xl/worksheets/s.xml");
  });

  it("resolves shared strings, booleans, inline strings and numbers as stored", () => {
    const shared = ["Name", "Qty", "Pen"];
    const root = sheet(
      row(c("A1", "0", "s"), c("B1", "1", "s")),
      row(c("A2", "2", "s"), c("B2", "45292.5")),
      row(c("A3", "1", "b"), el("c", { r: "B3", t: "inlineStr" }, el("is", {}, el("t", {}, "inline")))),
    );
    const read = readSheet(root, shared);
    expect(read.rows).toEqual([["Name", "Qty"], ["Pen", "45292.5"], ["TRUE", "inline"]]);
    expect(read.totalRows).toBe(3);
    expect(read.totalCols).toBe(2);
  });

  it("keeps a gap as an empty cell and skips a row with no value", () => {
    const read = readSheet(sheet(row(c("A1", "x"), c("C1", "z")), row(el("c", { r: "A2" })), row(c("B3", "y"))), []);
    expect(read.rows).toEqual([["x", "", "z"], ["", "y"]]);
    expect(read.totalRows).toBe(2);
  });

  it("caps rows and columns, and counts what it cut", () => {
    const letters = (j: number) => (j < 26 ? String.fromCharCode(65 + j) : `A${String.fromCharCode(65 + j - 26)}`);
    const rows = Array.from({ length: 205 }, (_, i) => row(...Array.from({ length: 35 }, (_, j) => c(`${letters(j)}${i + 1}`, "1"))));
    const read = readSheet(sheet(...rows), []);
    expect(read.rows).toHaveLength(200);
    expect(Math.max(...read.rows.map((r) => r.length))).toBe(30);
    expect(read.totalRows).toBe(205);
    expect(read.totalCols).toBe(35);
    const md = sheetToMarkdown("Big", read);
    expect(md).toContain("*Showing the first 200 of 205 rows and 30 of 35 columns.*");
  });

  it("writes a table with the first row as header, and escapes pipes and marks", () => {
    const md = sheetToMarkdown("Q1 | 2", { rows: [["a|b", "*c*"], ["1", "2"]], totalRows: 2, totalCols: 2 });
    expect(md).toBe("## Q1 | 2\n\n| a\\|b | \\*c\\* |\n| --- | --- |\n| 1 | 2 |");
  });

  it("says an empty sheet is empty", () => {
    expect(sheetToMarkdown("Blank", { rows: [], totalRows: 0, totalCols: 0 })).toBe("## Blank\n\n*This sheet is empty.*");
  });
});
