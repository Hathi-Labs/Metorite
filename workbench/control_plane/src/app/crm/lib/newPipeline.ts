// ── A new deal pipeline (WS-CRM CRM-M3a) ──────────────────────────────────
//
// The rules of the "New pipeline" dialog of CRM settings, pure, so they have
// tests without a browser (`components/crmM3.test.ts`).
//
// ⚠️ The gateway plans the pipeline, and this file holds no copy of the plan.
// `GET /crm/pipelines/preview` reuses a stage by its name, adds the org's won
// and lost stages, and refuses with words. The dialog shows that answer as its
// preview, and the chat card shows the same answer. This file only builds the
// request and checks what the member typed, so a field error shows at once.

import type { DealPipeline } from "./types";

/** The types that a stage of a new pipeline can take in the dialog. */
export type NewStageType = "open" | "won" | "lost";

/** One row of the stage list. `key` keeps a row when the rows move. */
export type NewStageRow = { key: string; name: string; type: NewStageType };

/** Where a new pipeline starts. */
export type PipelineStart = "copy" | "empty";

export type NewPipelineDraft = {
  name: string;
  start: PipelineStart;
  /** The id of the pipeline to copy, when `start` is `copy`. */
  copyFrom: string | null;
  stages: NewStageRow[];
};

/** One stage of the gateway's plan. `id` is null for a stage that the create makes. */
export type PlannedStage = {
  id: string | null;
  name: string;
  type: string;
  source: "existing" | "new" | "added";
};

/** The answer of `GET /crm/pipelines/preview`. */
export type PipelinePlan = {
  name: string;
  kind: string;
  copy_of: string | null;
  stages: PlannedStage[];
};

/** The longest name, as the gateway counts it (`pipelines.MAX_NAME`). */
export const PIPELINE_NAME_MAX = 120;

/** The most stages in one list (`pipelines.MAX_STAGES`). */
export const MAX_NEW_STAGES = 50;

/** The words of each type in the stage list. */
export const NEW_STAGE_TYPE_LABELS: Record<NewStageType, string> = {
  open: "Open",
  won: "Won",
  lost: "Lost",
};

/** What each stage of the plan is, in the words of the preview. */
export const PLAN_SOURCE_WORDS: Record<PlannedStage["source"], string> = {
  existing: "Shared",
  new: "New",
  added: "Added",
};

/** The help line of each source, for the tip and the screen reader. */
export const PLAN_SOURCE_HELP: Record<PlannedStage["source"], string> = {
  existing: "A stage the CRM has. The pipelines share it.",
  new: "The create makes this stage.",
  added: "The CRM adds its own stage, so that a deal in this pipeline can close.",
};

let rowCounter = 0;

/** A blank row with a new key. */
export function blankRow(): NewStageRow {
  rowCounter += 1;
  return { key: `row-${rowCounter}`, name: "", type: "open" };
}

/** The pipeline that a copy starts from: the default one, else the first. */
export function defaultSource(pipelines: readonly DealPipeline[] | null | undefined): string | null {
  if (!pipelines || pipelines.length === 0) return null;
  const sorted = [...pipelines].sort((a, b) => a.position - b.position);
  return (pipelines.find((p) => p.is_default) ?? sorted[0]).id;
}

/** The draft of a dialog that opens. With a pipeline, it starts as a copy. */
export function emptyDraft(pipelines: readonly DealPipeline[] | null | undefined): NewPipelineDraft {
  const source = defaultSource(pipelines);
  return {
    name: "",
    start: source ? "copy" : "empty",
    copyFrom: source,
    stages: [blankRow()],
  };
}

/**
 * One row as the route takes it. An open row sends no type: a name that the
 * CRM has keeps the type of that stage, and a new name becomes an open stage.
 * Won and Lost are a choice, so they go.
 */
function stageBody(row: NewStageRow): Record<string, string> {
  const name = row.name.trim();
  return row.type === "open" ? { name } : { name, type: row.type };
}

/** The named rows, in order. A blank row is a row still to fill, never a stage. */
export function namedRows(rows: readonly NewStageRow[]): NewStageRow[] {
  return rows.filter((row) => row.name.trim() !== "");
}

/**
 * The body of the create and of the preview, or null while the draft has
 * nothing to plan: no name, no pipeline to copy, or no named stage.
 */
export function pipelineBody(draft: NewPipelineDraft): Record<string, unknown> | null {
  const name = draft.name.trim();
  if (!name) return null;
  if (draft.start === "copy") {
    return draft.copyFrom ? { name, copy_from: draft.copyFrom } : null;
  }
  const rows = namedRows(draft.stages);
  return rows.length > 0 ? { name, stages: rows.map(stageBody) } : null;
}

/** A field error of the draft, in words, or null. The gateway checks the rest. */
export function draftProblem(draft: NewPipelineDraft): string | null {
  const name = draft.name.trim();
  if (!name) return "Give the pipeline a name.";
  if (name.length > PIPELINE_NAME_MAX) {
    return `A pipeline name has at most ${PIPELINE_NAME_MAX} characters.`;
  }
  if (draft.start === "copy") {
    return draft.copyFrom ? null : "Pick the pipeline to copy.";
  }
  const rows = namedRows(draft.stages);
  if (rows.length === 0) return "Add at least one stage.";
  if (rows.length > MAX_NEW_STAGES) return `A pipeline holds at most ${MAX_NEW_STAGES} stages.`;
  const seen = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim();
    if (name.length > PIPELINE_NAME_MAX) {
      return `The stage “${name.slice(0, 40)}…” has more than ${PIPELINE_NAME_MAX} characters.`;
    }
    const key = name.split(/\s+/).join(" ").toLowerCase();
    if (seen.has(key)) return `The list names “${name}” twice. Keep it once.`;
    seen.add(key);
  }
  return null;
}

/** The rows with the row at `from` moved to `to`. Out of range: the same rows. */
export function moveRow(rows: readonly NewStageRow[], from: number, to: number): NewStageRow[] {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) {
    return [...rows];
  }
  const next = [...rows];
  const [row] = next.splice(from, 1);
  next.splice(to, 0, row);
  return next;
}

/** The rows less one. The last row stays, blank, so the list is never empty. */
export function removeRow(rows: readonly NewStageRow[], key: string): NewStageRow[] {
  const next = rows.filter((row) => row.key !== key);
  return next.length > 0 ? next : [blankRow()];
}

/** The key of a body, so the dialog asks the preview once for each change. */
export function previewKey(body: Record<string, unknown> | null): string | null {
  return body ? JSON.stringify(body) : null;
}
