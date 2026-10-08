/**
 * dataGridLayout — the column rules of the `dataGrid` template, and when it
 * stacks its rows.
 *
 * Follow-up of #716 and #735 (spec `projects_ai_chat.md` §24.8). The owner's
 * dataset table showed two defects in the Projects rail:
 *
 * - The status category drew its raw value ("in_progress", "todo") in a
 *   column of its own, beside a Status column that already said the same.
 * - The Title column wrapped one word per line, and the last column ("Type")
 *   was cut at the edge with nothing to say that the table scrolls.
 *
 * So the template now draws by these rules:
 *
 * 1. **A column draws by its kind.** The kind comes from the data (`kinds`),
 *    else from the column's card label (`kindForLabel`), the one label map.
 *    A status is the status chip, a category its readable label in a chip, a
 *    tag a tag pill, a person a person chip, a date a formatted date.
 * 2. **A category column hides when a status column is there.** The status
 *    chip already says it, and the hidden cell gives the chip its colour.
 * 3. **The title column has priority.** It has a minimum width and wraps.
 *    The other columns do not wrap.
 * 4. **Under a width, the rows stack.** The table needs the sum of its
 *    columns' minimum widths. In a narrower box each row is a block: the
 *    number and the title on one line, the other cells as chips and
 *    labelled facts under it. A wider table that still overflows shows the
 *    "More columns to the right" cue. A column is never cut with no cue.
 *
 * Pure and framework-free. Fence: `src/lib/dataGridLayout.test.ts`.
 */

import { type FieldKind, kindForLabel } from "@/lib/cardFields";

/** How a cell draws. */
export type CellKind = "status" | "category" | "tag" | "person" | "date" | "text";

export interface GridColumn {
  /** The index of this column in each row's cells. */
  index: number;
  label: string;
  kind: CellKind;
  /** `lead` is the row number, `primary` the title, and the rest are `secondary`. */
  role: "lead" | "primary" | "secondary";
}

export interface GridColumns {
  /** The columns the grid draws, in order. */
  shown: GridColumn[];
  /** The index of the column that gives a status chip its colour, or -1. */
  categoryAt: number;
  /** The index of the column that carries a row's link. */
  primaryAt: number;
}

const DRAWN_KINDS: ReadonlySet<string> = new Set(["status", "category", "tag", "person", "date"]);

function cellKind(kind: FieldKind | string | undefined): CellKind {
  return kind && DRAWN_KINDS.has(kind) ? (kind as CellKind) : "text";
}

const LEAD = /^(?:#|no\.?|number)$/i;
const PRIMARY = /^(?:title|name|subject|task)$/i;

/** The columns to draw, from the labels and the kinds the data names. */
export function gridColumns(labels: readonly string[], kinds?: readonly (string | null | undefined)[]): GridColumns {
  const all = labels.map((label, index) => ({
    index,
    label,
    kind: cellKind(kinds?.[index] ?? kindForLabel(label)),
  }));
  const lead = all.length > 1 && LEAD.test(all[0].label.trim()) ? 0 : -1;
  const named = all.find((c) => c.index !== lead && PRIMARY.test(c.label.trim()));
  const primaryAt = named?.index ?? all.find((c) => c.index !== lead)?.index ?? 0;
  const hasStatus = all.some((c) => c.kind === "status");
  const category = hasStatus ? all.find((c) => c.kind === "category") : undefined;
  const shown: GridColumn[] = all
    .filter((c) => c.index !== category?.index)
    .map((c) => ({
      ...c,
      role: c.index === lead ? "lead" : c.index === primaryAt ? "primary" : "secondary",
    }));
  return { shown, categoryAt: category?.index ?? -1, primaryAt };
}

/** The least width each column takes in the table, in rem. */
export const MIN_REM: Record<"lead" | "primary" | CellKind, number> = {
  lead: 3,
  primary: 10,
  status: 6.5,
  category: 6.5,
  person: 6.5,
  date: 5.5,
  tag: 5.5,
  text: 5,
};

/** The least width of the whole table, in rem. */
export function minTableRem(shown: readonly GridColumn[]): number {
  return shown.reduce((sum, c) => sum + (c.role === "secondary" ? MIN_REM[c.kind] : MIN_REM[c.role]), 0);
}

export type GridLayout = "table" | "stacked";

/**
 * The table, or the stacked rows. `width` is the box's width in px, and
 * `remPx` is the root font size, so the rule follows the density. A box that
 * has not been measured (0) draws the table, as the server render does.
 */
export function gridLayout(width: number, shown: readonly GridColumn[], remPx = 16): GridLayout {
  if (!(width > 0) || shown.length <= 2) return "table";
  return width < minTableRem(shown) * remPx ? "stacked" : "table";
}

/**
 * The values of a cell that holds several: an array as it is, or a text
 * split at each comma that is not inside brackets ("Priya (a, b), Sam").
 */
export function splitMany(cell: unknown): string[] {
  if (Array.isArray(cell)) return cell.map((c) => String(c ?? "").trim()).filter(Boolean);
  const text = String(cell ?? "");
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of text) {
    if (ch === "(" || ch === "<" || ch === "[") depth++;
    if (ch === ")" || ch === ">" || ch === "]") depth = Math.max(0, depth - 1);
    if (ch === "," && depth === 0) {
      out.push(cur);
      cur = "";
      continue;
    }
    cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim()).filter(Boolean);
}

/** A cell as one string: for a sort, a compare and a tooltip. */
export function cellText(cell: unknown): string {
  if (Array.isArray(cell)) return cell.map((c) => String(c ?? "")).join(", ");
  return cell == null ? "" : String(cell);
}

/** A cell that says there is nothing: drawn muted, never as a chip. */
export function isEmptyCell(cell: unknown): boolean {
  return /^(?:|-|—|none|unassigned|no status)$/i.test(cellText(cell).trim());
}
