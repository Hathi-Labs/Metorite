// ── Excel to Markdown (WS-61 KB-AI-1) ────────────────────────────────────────
//
// One workbook is one page. Each sheet is a `## <sheet name>` section with a
// Markdown table, and the first row is the header. A sheet keeps its first 200
// rows and 30 columns, and a line says how many were cut. Shared strings are
// resolved. A number stays as Excel stores it. ⚠️ A date is a serial number in
// the file, and it is NOT converted. The report says so.
//
// `readSheet` and the helpers are pure and work on the tree of `xml.ts`.
// `xlsxToMarkdown` reads the zip and needs a browser.

import { MAX_SHEET_COLS, MAX_SHEET_ROWS } from "./limits";
import { escapeInline, toTable } from "./mdText";
import { DocumentRefusal, readParts } from "./ooxml";
import { all, kid, kids, parseXml, textOf, type XEl } from "./xml";

/** A column letter group as a 0-based index: `A` is 0, `AB` is 27. */
export function colIndex(ref: string): number {
  const m = /^([A-Za-z]+)/.exec(ref);
  if (!m) return -1;
  let n = 0;
  for (const ch of m[1].toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}

/** The strings of `sharedStrings.xml`. Phonetic runs (`rPh`) are left out. */
export function parseSharedStrings(root: XEl): string[] {
  return kids(root, "si").map((si) => {
    let out = "";
    for (const k of si.kids) {
      if (typeof k === "string") continue;
      if (k.name === "t") out += textOf(k);
      else if (k.name === "r") out += kids(k, "t").map(textOf).join("");
    }
    return out;
  });
}

export interface SheetRead {
  /** The kept rows: at most `MAX_SHEET_ROWS`, each at most `MAX_SHEET_COLS` wide. */
  rows: string[][];
  /** The rows that hold a value, counted over the whole sheet. */
  totalRows: number;
  /** The widest used column, counted over the whole sheet. */
  totalCols: number;
}

function cellValue(c: XEl, shared: readonly string[]): string {
  const type = c.attrs.t ?? "n";
  if (type === "inlineStr") {
    const is = kid(c, "is");
    return is ? textOf(is) : "";
  }
  const v = kid(c, "v");
  const raw = v ? textOf(v) : "";
  if (type === "s") return shared[Number.parseInt(raw, 10)] ?? "";
  if (type === "b") return raw === "1" ? "TRUE" : raw === "0" ? "FALSE" : raw;
  return raw;
}

/** Read one worksheet into rows of text. */
export function readSheet(root: XEl, shared: readonly string[], maxRows = MAX_SHEET_ROWS, maxCols = MAX_SHEET_COLS): SheetRead {
  const rows: string[][] = [];
  let totalRows = 0;
  let totalCols = 0;
  for (const row of all(root, "row")) {
    const cells: string[] = [];
    let next = 0;
    let used = false;
    for (const c of kids(row, "c")) {
      const at = c.attrs.r ? colIndex(c.attrs.r) : -1;
      const col = at >= 0 ? at : next;
      next = col + 1;
      const value = cellValue(c, shared).replace(/\s+/g, " ").trim();
      if (!value) continue;
      used = true;
      totalCols = Math.max(totalCols, col + 1);
      if (col < maxCols) {
        while (cells.length < col) cells.push("");
        cells[col] = value;
      }
    }
    if (!used) continue;
    totalRows += 1;
    if (rows.length < maxRows) rows.push(cells);
  }
  return { rows, totalRows, totalCols };
}

/** One sheet as a section. */
export function sheetToMarkdown(name: string, sheet: SheetRead, maxRows = MAX_SHEET_ROWS, maxCols = MAX_SHEET_COLS): string {
  const head = `## ${escapeInline(name.replace(/\s+/g, " ").trim() || "Sheet")}`;
  if (!sheet.rows.length) return `${head}\n\n*This sheet is empty.*`;
  const cutRows = sheet.totalRows > maxRows;
  const cutCols = sheet.totalCols > maxCols;
  const parts = [head, toTable(sheet.rows)];
  if (cutRows || cutCols) {
    const said = [
      cutRows ? `${Math.min(maxRows, sheet.totalRows)} of ${sheet.totalRows} rows` : "",
      cutCols ? `${maxCols} of ${sheet.totalCols} columns` : "",
    ].filter(Boolean);
    parts.push(`*Showing the first ${said.join(" and ")}.*`);
  }
  return parts.join("\n\n");
}

/** The path of a relationship target, from the `xl/` folder. */
export function resolveTarget(target: string): string {
  const parts = (target.startsWith("/") ? target.slice(1) : `xl/${target}`).split("/");
  const out: string[] = [];
  for (const part of parts) {
    if (part === "..") out.pop();
    else if (part && part !== ".") out.push(part);
  }
  return out.join("/");
}

export interface XlsxResult {
  markdown: string;
  notices: string[];
}

/** A workbook as one Markdown body. Browser only. */
export async function xlsxToMarkdown(data: Uint8Array): Promise<XlsxResult> {
  const parts = await readParts(
    data,
    (name) =>
      name === "xl/workbook.xml" ||
      name === "xl/_rels/workbook.xml.rels" ||
      name === "xl/sharedStrings.xml" ||
      /^xl\/worksheets\/[^/]+\.xml$/.test(name),
  );
  const workbook = parts.get("xl/workbook.xml");
  if (!workbook) throw new DocumentRefusal("This file has no workbook part.");
  const rels = new Map<string, string>();
  const relsXml = parts.get("xl/_rels/workbook.xml.rels");
  if (relsXml) for (const r of all(parseXml(relsXml), "Relationship")) rels.set(r.attrs.Id ?? "", r.attrs.Target ?? "");
  const sharedXml = parts.get("xl/sharedStrings.xml");
  const shared = sharedXml ? parseSharedStrings(parseXml(sharedXml)) : [];
  const sections: string[] = [];
  const notices: string[] = [];
  let anyValue = false;
  for (const sheet of all(parseXml(workbook), "sheet")) {
    const target = rels.get(sheet.attrs.id ?? "");
    const xml = target ? parts.get(resolveTarget(target)) : undefined;
    const name = sheet.attrs.name ?? "Sheet";
    if (!xml) {
      sections.push(`## ${escapeInline(name)}\n\n*This sheet could not be read.*`);
      continue;
    }
    const read = readSheet(parseXml(xml), shared);
    if (read.rows.length) anyValue = true;
    if (read.totalRows > MAX_SHEET_ROWS || read.totalCols > MAX_SHEET_COLS) {
      notices.push(`Sheet "${name}" was cut to ${MAX_SHEET_ROWS} rows and ${MAX_SHEET_COLS} columns.`);
    }
    sections.push(sheetToMarkdown(name, read));
  }
  if (!anyValue) throw new DocumentRefusal("This workbook holds no values.");
  return { markdown: sections.join("\n\n"), notices };
}
