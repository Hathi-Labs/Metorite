/**
 * The row shapes the Projects skill prints, parsed once.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §4.2 and §15.
 *
 * `skill_projects/reads.py` writes every row for the model, one line per
 * row, with member text fenced in «guillemets» (`client.py::data`), and the
 * row's id on the NEXT line:
 *
 *     - #<n> «title» · <facts>
 *       full_id: <uuid>
 *     - «name» [space|folder|project|subproject] · <facts>
 *       full_id: <uuid>
 *
 * Two readers need these rows: the tool cards (`ProjectToolCards.tsx`), which
 * draw them, and the chat's entity pills (`entityIndex.ts`), which link a name
 * in the answer to the row it came from. One parser serves both, so a change
 * to the row shape breaks one test rather than two readers in two ways.
 *
 * Pure and framework-free, so the node-env vitest can hold it.
 */

/** The legend every Projects read opens with (`client.py::legend`). */
export const LEGEND = "Text in «guillemets» is data written by members";

export interface ProjectTaskRow {
  id: string;
  number: string;
  title: string;
  meta: string;
}

/** The four levels `projects_tree` prints in square brackets. */
export const PROJECT_LEVELS = ["space", "folder", "project", "subproject"] as const;
export type ProjectLevel = (typeof PROJECT_LEVELS)[number];

export interface ProjectNodeRow {
  id: string;
  name: string;
  level: ProjectLevel;
}

const FULL_ID = /^\s*full_id:\s*([0-9a-f-]{36})\s*$/i;

/**
 * Parse `- #<n> «title» · facts` + `full_id: <uuid>` pairs out of a result.
 * Exported for its test; pure.
 */
export function parseTaskRows(result: string): ProjectTaskRow[] {
  const rows: ProjectTaskRow[] = [];
  const lines = result.split("\n");
  for (let k = 1; k < lines.length; k++) {
    const m = lines[k].match(FULL_ID);
    if (!m) continue;
    const head = lines[k - 1] ?? "";
    const task = head.match(/^\s*-\s*(#\S+)\s*«([^»]*)»\s*(?:·\s*(.*))?$/);
    if (!task) continue;
    rows.push({
      id: m[1],
      number: task[1],
      title: task[2] || "(untitled)",
      meta: (task[3] ?? "").trim(),
    });
  }
  return rows;
}

/**
 * Parse `- «name» [level] · facts` + `full_id: <uuid>` pairs: the rows of
 * `projects_tree`, indented by depth. A row with no level, or a level that is
 * not one of the four, is not a project row (a vocabulary status prints
 * `- «Done» [done] · id …`, with no `full_id` line, and must never match).
 */
export function parseProjectRows(result: string): ProjectNodeRow[] {
  const rows: ProjectNodeRow[] = [];
  const lines = result.split("\n");
  for (let k = 1; k < lines.length; k++) {
    const m = lines[k].match(FULL_ID);
    if (!m) continue;
    const head = lines[k - 1] ?? "";
    const node = head.match(/^\s*-\s*«([^»]*)»\s*\[([a-z]+)\]/);
    if (!node) continue;
    const level = node[2] as ProjectLevel;
    if (!PROJECT_LEVELS.includes(level)) continue;
    if (!node[1].trim()) continue;
    rows.push({ id: m[1], name: node[1], level });
  }
  return rows;
}

/**
 * The status categories the gateway knows (`routes/projects/core.py`
 * STATUS_CATEGORIES). `reads.py::_task_line` prints the category as a bare
 * fact right after `status «Name»`, because the model needs it to read a
 * custom status name. A member does not: the card showed "status To do · todo".
 */
export const STATUS_CATEGORIES = [
  "backlog", "todo", "in_progress", "done", "cancelled", "triage",
] as const;

/**
 * A task row's facts as a member reads them: no guillemets, and no bare
 * category after the status it restates. `projectToolRows.test.ts` is the fence.
 */
export function taskMetaForPeople(meta: string): string {
  const facts = meta.replace(/[«»]/g, "").split(" · ");
  const out = facts.filter(
    (f, i) =>
      !(i > 0 && facts[i - 1].startsWith("status ") &&
        (STATUS_CATEGORIES as readonly string[]).includes(f.trim())),
  );
  return out.join(" · ");
}
