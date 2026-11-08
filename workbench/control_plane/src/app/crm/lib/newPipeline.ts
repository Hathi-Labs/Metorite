// ── A new deal or lead pipeline (WS-CRM CRM-M3a, CRM-M4a) ─────────────────
//
// The rules of the "New pipeline" dialog of CRM settings, pure, so they have
// tests without a browser (`components/crmM3.test.ts`, `crmM4.test.ts`). One
// dialog serves both kinds. A lead pipeline's stages are lead statuses, and it
// names the deal pipeline that it feeds (`feeds`).
//
// ⚠️ The gateway plans the pipeline, and this file holds no copy of the plan.
// `GET /crm/pipelines/preview` reuses a stage by its name, adds the org's won
// and lost stages, and refuses with words. The dialog shows that answer as its
// preview, and the chat card shows the same answer. This file only builds the
// request and checks what the member typed, so a field error shows at once.

import type { DealPipeline } from "./types";

/** The types that a stage of a new pipeline can take in the dialog. */
export type NewStageType = "open" | "won" | "lost";

/** The two kinds of pipeline (CRM-M4a). The gateway names them the same. */
export type PipelineKind = "deal" | "lead";

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
  /**
   * CRM-M4a, a lead pipeline only: the NAME of the deal pipeline that it
   * feeds, as the gateway takes it. Null or absent: the default deal pipeline.
   */
  feeds?: string | null;
};

/** One stage of the gateway's plan. `id` is null for a stage that the create makes. */
export type PlannedStage = {
  id: string | null;
  name: string;
  type: string;
  source: "existing" | "new" | "added";
  /** A hidden stage of a copied pipeline. It counts for no check. */
  hidden?: boolean;
};

/** The answer of `GET /crm/pipelines/preview`. */
export type PipelinePlan = {
  name: string;
  kind: string;
  copy_of: string | null;
  stages: PlannedStage[];
  /** CRM-M4a: the deal pipeline that a lead pipeline feeds. Null: the default one. */
  feeds?: string | null;
};

/** The words of one kind of pipeline in the dialog and the preview (CRM-M4a). */
export type PipelineWords = {
  title: string;
  description: string;
  /** The name of a pipeline, as an example. */
  example: string;
  /** "pipeline" or "lead pipeline", in a sentence. */
  pipeline: string;
  stage: string;
  stages: string;
  /** The noun of one stage at the start of a label. */
  Stage: string;
  record: string;
  create: string;
};

export const PIPELINE_WORDS: Record<PipelineKind, PipelineWords> = {
  deal: {
    title: "New pipeline",
    description: "The stages that one kind of deal moves through, in order.",
    example: "AMC contracts",
    pipeline: "pipeline",
    stage: "stage",
    stages: "stages",
    Stage: "Stage",
    record: "deal",
    create: "Create pipeline",
  },
  lead: {
    title: "New lead pipeline",
    description: "The statuses that one kind of lead moves through before it converts, in order.",
    example: "Trade show",
    pipeline: "lead pipeline",
    stage: "status",
    stages: "statuses",
    Stage: "Status",
    record: "lead",
    create: "Create lead pipeline",
  },
};

/** The kind of a plan, as the gateway names it. Any other word reads as a deal. */
export function kindOf(plan: { kind?: string } | null | undefined): PipelineKind {
  return plan?.kind === "lead" ? "lead" : "deal";
}

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

/** The help line of each source in the words of one kind (CRM-M4a). */
export function planSourceHelp(source: PlannedStage["source"], kind: PipelineKind): string {
  if (kind === "deal") return PLAN_SOURCE_HELP[source];
  const w = PIPELINE_WORDS[kind];
  return {
    existing: `A ${w.stage} the CRM has. The pipelines share it.`,
    new: `The create makes this ${w.stage}.`,
    added: `The CRM adds its own ${w.stage}, so that a ${w.record} in this pipeline can close.`,
  }[source];
}

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

/**
 * The draft of a dialog that opens. With a pipeline of its kind, it starts as
 * a copy. A lead pipeline starts by feeding the default deal pipeline, by
 * name, so the member sees where its deals go (CRM-M4a).
 */
export function emptyDraft(
  pipelines: readonly DealPipeline[] | null | undefined,
  kind: PipelineKind = "deal",
  dealPipelines: readonly DealPipeline[] | null | undefined = null,
): NewPipelineDraft {
  const source = defaultSource(pipelines);
  const draft: NewPipelineDraft = {
    name: "",
    start: source ? "copy" : "empty",
    copyFrom: source,
    stages: [blankRow()],
  };
  if (kind === "lead") {
    const fed = defaultSource(dealPipelines);
    draft.feeds = dealPipelines?.find((p) => p.id === fed)?.name ?? null;
  }
  return draft;
}

/**
 * One row as the route takes it. A name of a stage that the CRM has sends no
 * type: that stage keeps its own, and the route refuses a type that differs.
 * An open row sends no type, so a new name becomes an open stage. Won and Lost
 * are a choice, so they go. A closing word that the member set to Open sends
 * "open", because the route reads a bare closing word as closed.
 */
function stageBody(
  row: NewStageRow,
  stored: ReadonlyMap<string, string>,
  kind: PipelineKind,
): Record<string, string> {
  const name = row.name.trim();
  if (stored.has(foldName(name))) return { name };
  if (row.type !== "open") return { name, type: row.type };
  return closingType(name, kind) ? { name, type: "open" } : { name };
}

/**
 * The closing words of the route, for each kind (`PipelineKind.closing_words`
 * of `routes/crm/pipelines.py`). A new stage with one and no type closes. For
 * a lead, won is "converted" and lost is "lost" or "junk".
 */
const CLOSING_WORDS: Record<PipelineKind, Record<string, NewStageType>> = {
  deal: { won: "won", "closed won": "won", lost: "lost", "closed lost": "lost" },
  lead: { won: "won", converted: "won", lost: "lost", junk: "lost" },
};

/** The type that the route gives a closing name, or null for any other name. */
export function closingType(name: string, kind: PipelineKind = "deal"): NewStageType | null {
  return CLOSING_WORDS[kind][foldName(name)] ?? null;
}

/**
 * A row with a new name. A closing word sets Won or Lost, as the route does,
 * so the row shows the type that the create makes. A name that stops being a
 * closing word takes back Open, if the type came from the old name.
 */
export function renameRow(row: NewStageRow, name: string, kind: PipelineKind = "deal"): NewStageRow {
  const before = closingType(row.name, kind);
  const after = closingType(name, kind);
  // The type came from the old name, so the new name may change it.
  const inferred = before !== null && row.type === before;
  if (after && (inferred || (row.type === "open" && before === null))) {
    return { ...row, name, type: after };
  }
  if (!after && inferred) return { ...row, name, type: "open" };
  return { ...row, name };
}

/** The named rows, in order. A blank row is a row still to fill, never a stage. */
export function namedRows(rows: readonly NewStageRow[]): NewStageRow[] {
  return rows.filter((row) => row.name.trim() !== "");
}

/**
 * The body of the create and of the preview, or null while the draft has
 * nothing to plan: no name, no pipeline to copy, or no named stage. `stored`
 * holds the stages that the CRM has (:func:`storedTypes`). Their rows send no
 * type. A deal body names no kind, as before CRM-M4a. A lead body names
 * `kind` and, when the draft holds one, `feeds`.
 */
export function pipelineBody(
  draft: NewPipelineDraft,
  stored: ReadonlyMap<string, string> = new Map(),
  kind: PipelineKind = "deal",
): Record<string, unknown> | null {
  const name = draft.name.trim();
  if (!name) return null;
  const head: Record<string, unknown> = { name };
  if (kind === "lead") {
    head.kind = "lead";
    const feeds = (draft.feeds ?? "").trim();
    if (feeds) head.feeds = feeds;
  }
  if (draft.start === "copy") {
    return draft.copyFrom ? { ...head, copy_from: draft.copyFrom } : null;
  }
  const rows = namedRows(draft.stages);
  return rows.length > 0
    ? { ...head, stages: rows.map((row) => stageBody(row, stored, kind)) }
    : null;
}

/** A field error of the draft, in words, or null. The gateway checks the rest. */
export function draftProblem(draft: NewPipelineDraft, kind: PipelineKind = "deal"): string | null {
  const w = PIPELINE_WORDS[kind];
  const name = draft.name.trim();
  if (!name) return `Give the ${w.pipeline} a name.`;
  if (name.length > PIPELINE_NAME_MAX) {
    return `A ${w.pipeline} name has at most ${PIPELINE_NAME_MAX} characters.`;
  }
  if (draft.start === "copy") {
    return draft.copyFrom ? null : `Pick the ${w.pipeline} to copy.`;
  }
  const rows = namedRows(draft.stages);
  if (rows.length === 0) return `Add at least one ${w.stage}.`;
  if (rows.length > MAX_NEW_STAGES) {
    return `A ${w.pipeline} holds at most ${MAX_NEW_STAGES} ${w.stages}.`;
  }
  const seen = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim();
    if (name.length > PIPELINE_NAME_MAX) {
      return `The ${w.stage} “${name.slice(0, 40)}…” has more than ${PIPELINE_NAME_MAX} characters.`;
    }
    const key = foldName(name);
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

/**
 * The type of each stage (or lead status) that the CRM has, by its folded name. A row
 * with such a name shows that type and sends none (:func:`pipelineBody`), so
 * the row and the body agree. It reads the stages of the store, so it holds
 * before a plan loads, after a read fails, and in the phone tree.
 */
export function storedTypes(statuses: readonly { name: string; type: string }[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const status of statuses) out.set(foldName(status.name), status.type);
  return out;
}

/**
 * A row whose name is a stage of the CRM drops a type that the member picked
 * before: that stage keeps its own. So a name that then changes to a new
 * stage starts at Open again, not at a stale pick.
 */
export function settleRow(row: NewStageRow, stored: ReadonlyMap<string, string>): NewStageRow {
  return row.type !== "open" && stored.has(foldName(row.name)) ? { ...row, type: "open" } : row;
}

/** A name as two names compare: inner spaces folded, in lower case. */
export function foldName(name: string): string {
  return name.trim().split(/\s+/).join(" ").toLowerCase();
}

/** The key of a body, so the dialog asks the preview once for each change. */
export function previewKey(body: Record<string, unknown> | null): string | null {
  return body ? JSON.stringify(body) : null;
}
