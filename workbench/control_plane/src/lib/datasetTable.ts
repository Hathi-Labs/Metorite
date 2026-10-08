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
 */

import { fieldSpec } from "@/lib/cardFields";
import { unfenced } from "@/lib/fencedText";
import { LEGEND } from "@/lib/projectToolRows";

export interface DatasetTable {
  title: string;
  columns: string[];
  rows: { id?: string; cells: string[] }[];
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
  const text = unfenced(line.trim());
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
  let k = headAt + 1;
  if (isGroups) {
    const by = titleLine.match(/^Groups by (\w+), measure (\w+)/);
    columns = [fieldSpec(by?.[1] ?? "group").label, measureLabel(by?.[2] ?? "count"), "Tasks"];
    let extra = false;
    for (; k < lines.length && /^- /.test(lines[k]); k++) {
      const [label = "", value = "", n = "", ...more] = splitDots(lines[k].slice(2));
      if (more.length) extra = true;
      rows.push({ cells: [unfenced(label), value, n, ...(more.length ? [more.join(", ")] : [])] });
    }
    if (extra) {
      columns.push("Note");
      for (const r of rows) while (r.cells.length < columns.length) r.cells.push("");
    }
  } else {
    const keys = lines[headAt].split("|").map((c) => c.trim());
    const idCol = keys.indexOf("full_id");
    columns = keys.filter((_, i) => i !== idCol).map((c) => fieldSpec(c).label);
    for (; k < lines.length && lines[k].includes("|"); k++) {
      const cells = lines[k].split("|").map((c) => unfenced(c.trim()));
      while (cells.length < keys.length) cells.push("");
      const id = idCol >= 0 && UUID.test(cells[idCol]) ? cells[idCol] : undefined;
      rows.push({
        ...(id ? { id } : {}),
        cells: cells.slice(0, keys.length).filter((_, i) => i !== idCol),
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
  return { title, columns, rows, caption, notes };
}
