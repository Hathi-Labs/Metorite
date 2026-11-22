/**
 * The paste of rows into the sheet of line items (STU-D17a, `ai_studio.md` D17). Pure: no React.
 *
 * The text is what Excel and Google Sheets put on the clipboard: tab between cells, a line break
 * between rows (CRLF or LF), a quoted cell when it holds a tab, a quote or a line break, and a
 * line break after the last row. `parseTsv` reads all of that.
 *
 * **The rules.**
 * 1. The first pasted column lands on the focused column. The paste fills the EDITABLE columns
 *    from there to the right. A read-only column is skipped. More cells than columns refuse the paste.
 * 2. The first pasted row lands on the focused row and goes down. A row past the end of the sheet
 *    makes a new row. With no focused row, every pasted row is a new row.
 * 3. A first row that matches the column labels (or keys) is a header and is skipped.
 * 4. Each cell goes through `cellValue` (`checkValue`). One bad cell refuses the whole paste, as
 *    "Row 3, Pct: ...". The row number is the line of the pasted text.
 * 5. The cap holds: new and changed rows together make at most 49 lines. A paste over the cap is
 *    refused BEFORE any call. It is never cut into chunks: a chunk breaks all or none, and the
 *    rule of a sum would refuse each chunk. A paste of more rows waits on the batch of E8.
 */
import { labelOf } from "./blocks";
import {
  MAX_SHEET_LINES, type SheetBuffer, type SheetColumn, addRow, cellValue, saveCount, withValue,
} from "./sheetModel";

/** Read tab-separated text into rows of cells. A trailing line break adds no row. */
export function parseTsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let atStart = true;
  let i = 0;
  const endCell = () => { row.push(cell); cell = ""; atStart = true; };
  const endRow = () => { endCell(); rows.push(row); row = []; };
  while (i < text.length) {
    const c = text[i];
    if (quoted) {
      if (c === "\"") {
        if (text[i + 1] === "\"") { cell += "\""; i += 2; continue; }
        quoted = false;
        i += 1;
        continue;
      }
      cell += c;
      i += 1;
      continue;
    }
    if (c === "\"" && atStart) { quoted = true; atStart = false; i += 1; continue; }
    if (c === "\t") { endCell(); i += 1; continue; }
    if (c === "\r" || c === "\n") {
      endRow();
      i += c === "\r" && text[i + 1] === "\n" ? 2 : 1;
      continue;
    }
    cell += c;
    atStart = false;
    i += 1;
  }
  // The last row has no line break, or the text ended with one (and the row is empty).
  if (cell !== "" || row.length > 0 || quoted) endRow();
  return rows;
}

export interface PasteFocus {
  /** The uid of the focused row, or null. */
  uid: string | null;
  /** The key of the focused column, or null. */
  key: string | null;
}

export type PasteResult =
  | { ok: true; buf: SheetBuffer; rows: number; headerSkipped: boolean }
  | { ok: false; message: string };

const norm = (s: string) => s.trim().toLowerCase();

const TRUE_WORDS = new Set(["true", "yes", "y", "1", "x", "✓"]);
const FALSE_WORDS = new Set(["false", "no", "n", "0", ""]);

/** Does the first row hold the names of the columns? Every filled cell must match its column. */
function isHeader(cells: readonly string[], cols: readonly SheetColumn[]): boolean {
  const filled = cells.filter((c) => c.trim() !== "");
  if (filled.length === 0) return false;
  return cells.every((c, i) => {
    if (c.trim() === "") return true;
    const f = cols[i]?.field;
    return !!f && (norm(c) === norm(labelOf(f)) || norm(c) === norm(f.key));
  });
}

/**
 * Paste `text` into the buffer. The result is a new buffer, or a refusal that changes nothing.
 * The check of each cell is a field check only: the door judges the rules.
 */
export function pasteInto(
  buf: SheetBuffer, columns: readonly SheetColumn[], text: string, focus: PasteFocus,
): PasteResult {
  const editable = columns.filter((c) => c.editable);
  if (editable.length === 0) return { ok: false, message: "No column of this sheet takes a paste." };
  let parsed = parseTsv(text);
  if (parsed.length === 0 || parsed.every((r) => r.every((c) => c.trim() === ""))) {
    return { ok: false, message: "The paste holds no cells." };
  }

  const at = focus.key === null ? -1 : columns.findIndex((c) => c.field.key === focus.key);
  const start = at < 0 ? 0 : columns.slice(0, at).filter((c) => c.editable).length;
  const span = editable.slice(start);

  let headerSkipped = false;
  let offset = 0;
  if (isHeader(parsed[0], span)) {
    parsed = parsed.slice(1);
    headerSkipped = true;
    offset = 1;
  }
  if (parsed.length === 0) return { ok: false, message: "The paste holds only a header row." };

  for (const [r, cells] of parsed.entries()) {
    if (cells.length > span.length) {
      return {
        ok: false,
        message: `Row ${r + 1 + offset} has ${cells.length} cells, but ${span.length} column${span.length === 1 ? "" : "s"} can take them from here.`,
      };
    }
  }

  let next = buf;
  const focusAt = focus.uid === null ? -1 : buf.rows.findIndex((row) => row.uid === focus.uid);
  const first = focusAt >= 0 ? focusAt : buf.rows.length;
  // A pasted row of blank cells leaves its row as it is.
  for (const [r, cells] of parsed.entries()) {
    const pos = first + r;
    if (cells.every((c) => c.trim() === "")) continue;
    while (next.rows.length <= pos) next = addRow(next);
    const uid = next.rows[pos].uid;
    for (const [c, raw] of cells.entries()) {
      const field = span[c].field;
      const label = labelOf(field);
      const draft = field.type === "checkbox"
        ? (TRUE_WORDS.has(norm(raw)) ? true : FALSE_WORDS.has(norm(raw)) ? false : null)
        : raw;
      if (draft === null) return { ok: false, message: `Row ${r + 1 + offset}, ${label}: Enter true or false.` };
      const checked = cellValue(field, draft);
      if (!checked.ok) return { ok: false, message: `Row ${r + 1 + offset}, ${label}: ${checked.message}` };
      next = withValue(next, uid, field.key, checked.value);
    }
  }
  const lines = saveCount(next);
  if (lines > MAX_SHEET_LINES) {
    return {
      ok: false,
      message: `A save holds at most ${MAX_SHEET_LINES} lines. This paste would make ${lines} new and changed lines, so nothing was pasted.`,
    };
  }
  return { ok: true, buf: next, rows: parsed.length, headerSkipped };
}
