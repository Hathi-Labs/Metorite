/**
 * datasetTable — the result of `task_dataset` as a table for a person.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24, rule 2, and §13.7
 * (S7e, the on-the-fly analysis). Owner report, 2026-10-08: the "Task
 * dataset" receipt showed the model's text as it is — a header line
 * `number | title | status | …`, rows like `#11 | «Task …» | To do | todo |
 * | Bug`, and the cycle note as a key and a value.
 *
 * `skill_projects/reads.py` prints two shapes for the model:
 *
 *     Tasks in «Metorite», state open:            the rows (`_dataset_rows`)
 *       cycle times: first in_progress to …
 *     number | title | status | …                 the header
 *     #11 | «Task doesn't disappear» | To do | …  one line per row
 *     rows=10 total=10 truncated=no scope=… state=open
 *
 *     Groups by tag, measure count, computed …    the groups (`_dataset_groups`)
 *       cycle times: …
 *     key · value · n
 *     - «Bug» · 4 · 4
 *     groups=3 of 3 total=10 truncated=no …
 *
 * This file turns either into columns and rows, with the «marks» gone
 * (`lib/fencedText.ts`) and each column named by its card label
 * (`lib/cardFields.ts`). The lines that speak to the model ("Label every
 * figure…", "These figures are exact…") are dropped. A note for a person
 * (the cycle window, a hidden column) keeps its first sentence.
 *
 * The server never puts a `|` inside a cell (`_fenced` turns it into `/`), so
 * a split on `|` is exact. Pure. Fence: `src/lib/datasetTable.test.ts`.
 *
 * Each column also carries its card KIND (follow-up of #716 and #735), so
 * the `dataGrid` template draws a status as its chip, a category as its
 * readable label, and tags and assignees as pills. A column of several
 * values (tags, assignees) gives each cell as a list of names, split at the
 * marks, so a name with a comma in it stays one name.
 */

import { type FieldKind, fieldSpec } from "@/lib/cardFields";
import { splitFenced, unfenced } from "@/lib/fencedText";
import { LEGEND } from "@/lib/projectToolRows";

/** One cell: a value, or the names of a column that holds several. */
export type DatasetCell = string | string[];

export interface DatasetTable {
  title: string;
  columns: string[];
  /** Each column's card kind, by index: how the template draws its cells. */
  kinds: (FieldKind | undefined)[];
  rows: { id?: string; cells: DatasetCell[] }[];
  /** "10 of 25 tasks", or "" when the trailer is missing. */
  caption: string;
  notes: string[];
}

/** Lines that speak only to the model. */
const FOR_MODEL = [
  /^(?:rows|groups)=/,
  /^TRUNCATED:/,
  /^Label every figure/,
  /^These figures are exact/,
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function firstSentence(line: string): string {
  // "first in_progress to first done": a category key in a note reads as words.
  const text = unfenced(line.trim()).replace(/\b([a-z]+)_([a-z]+)\b/g, "$1 $2");
  const m = text.match(/^(.+?[.!?])(?:\s|$)/);
  const one = m ? m[1] : text;
  return one.charAt(0).toUpperCase() + one.slice(1);
}

/** Split on ` · ` outside a «fenced» name. */
function splitDots(line: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "«") depth++;
    if (ch === "»") depth = Math.max(0, depth - 1);
    if (depth === 0 && line.startsWith(" · ", i)) {
      out.push(cur);
      cur = "";
      i += 2;
      continue;
    }
    cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

/** A cell of a column that holds several values, as its names. */
function namesOf(cell: string): string[] {
  const names = splitFenced(cell).filter((p) => p.kind === "name").map((p) => p.text);
  return names.length > 0 ? names : unfenced(cell).split(",").map((t) => t.trim()).filter(Boolean);
}

function measureLabel(measure: string): string {
  const words = measure.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Value";
}

function captionOf(trailer: string): string {
  const shown = trailer.match(/^(?:rows|groups)=(\d+)/)?.[1];
  const total = trailer.match(/\btotal=(\d+)/)?.[1];
  const groups = trailer.startsWith("groups=");
  if (!shown) return "";
  if (groups) {
    const of = trailer.match(/^groups=\d+ of (\d+)/)?.[1];
    return `${shown}${of && of !== shown ? ` of ${of}` : ""} groups · ${total ?? "?"} tasks`;
  }
  return total && total !== shown ? `${shown} of ${total} tasks` : `${shown} tasks`;
}

/**
 * The table in a `task_dataset` result, or null when the text holds neither
 * shape (a refusal such as "measure needs group_by", or a gateway error).
 */
export function parseDatasetTable(result: string): DatasetTable | null {
  const lines = (result || "")
    .split(/\r?\n/)
    .filter((l) => !l.startsWith(LEGEND));
  const pipeAt = lines.findIndex((l) => l.includes("|"));
  const groupAt = lines.findIndex((l) => l.trim() === "key · value · n");
  if (pipeAt < 0 && groupAt < 0) return null;
  const isGroups = groupAt >= 0 && (pipeAt < 0 || groupAt < pipeAt);
  const headAt = isGroups ? groupAt : pipeAt;

  const before = lines.slice(0, headAt).filter((l) => l.trim());
  const titleLine = before.find((l) => !/^\s/.test(l)) ?? "";
  const title = unfenced(titleLine.trim().replace(/:$/, ""));
  const notes: string[] = before.filter((l) => l !== titleLine).map(firstSentence);

  const rows: DatasetTable["rows"] = [];
  let columns: string[];
  let kinds: (FieldKind | undefined)[];
  let k = headAt + 1;
  if (isGroups) {
    const by = titleLine.match(/^Groups by (\w+), measure (\w+)/);
    const group = fieldSpec(by?.[1] ?? "group");
    columns = [group.label, measureLabel(by?.[2] ?? "count"), "Tasks"];
    kinds = [group.kind, undefined, undefined];
    let extra = false;
    for (; k < lines.length && /^- /.test(lines[k]); k++) {
      const [label = "", value = "", n = "", ...more] = splitDots(lines[k].slice(2));
      if (more.length) extra = true;
      rows.push({ cells: [unfenced(label), value, n, ...(more.length ? [more.join(", ")] : [])] });
    }
    if (extra) {
      columns.push("Note");
      kinds.push(undefined);
      for (const r of rows) while (r.cells.length < columns.length) r.cells.push("");
    }
  } else {
    const keys = lines[headAt].split("|").map((c) => c.trim());
    const idCol = keys.indexOf("full_id");
    const specs = keys.map((c) => fieldSpec(c));
    columns = specs.filter((_, i) => i !== idCol).map((s) => s.label);
    kinds = specs.filter((_, i) => i !== idCol).map((s) => s.kind);
    for (; k < lines.length && lines[k].includes("|"); k++) {
      const raw = lines[k].split("|").map((c) => c.trim());
      while (raw.length < keys.length) raw.push("");
      const cells: DatasetCell[] = raw
        .slice(0, keys.length)
        .map((c, i) => (specs[i].many ? namesOf(c) : unfenced(c)));
      const id = idCol >= 0 && UUID.test(String(cells[idCol])) ? String(cells[idCol]) : undefined;
      rows.push({
        ...(id ? { id } : {}),
        cells: cells.filter((_, i) => i !== idCol),
      });
    }
  }

  let caption = "";
  for (const line of lines.slice(k)) {
    const t = line.trim();
    if (!t) continue;
    if (/^(?:rows|groups)=/.test(t)) {
      caption = captionOf(t);
      continue;
    }
    if (FOR_MODEL.some((re) => re.test(t))) continue;
    notes.push(firstSentence(t));
  }
  return { title, columns, kinds, rows, caption, notes };
}
