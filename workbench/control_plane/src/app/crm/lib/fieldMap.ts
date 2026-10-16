// ── The Field map: which Zoho field fills which column (WS-53 CRM-Q7a) ─────
//
// Pure, so the vitest setup (no DOM) can test every rule. `FieldMapCard.tsx`
// draws what this file decides. The gateway serves the map at
// `GET /crm/field-rules?entity=`, and it decides what is legal: each field
// carries the columns it may fill (`targets`). This file holds no copy of the
// type or the layout rules.
//
// Spec: project-docs/specs/crm_platform.md section 18.7, "CRM-Q7a".
//
// CRM-Q18-2 (section 18.9.6) adds the plan: the versions of the Field map.
// The gateway keeps them at `/crm/mapping/plans`. One version is in use. A
// pick by hand goes into a draft, and one press approves the draft. "Go back"
// approves the content of the version before. The words of this file never
// name a version by its number, and never use the engine words of the
// gateway ("snapshot", "superseded"). Fence: `components/fieldMapPlan.test.ts`.

import type { BadgeTone } from "@/components/ui/Badge";
import type { SelectOption } from "@/components/ui/SelectButton";
import { CrmError } from "./api";
import { humanizeKey } from "./connect";
import { dateTime } from "./format";

export type FieldRuleStatus = "proposed" | "approved" | "rejected" | "stale";

/** One row of `crm_field_rules`, as the gateway sends it. */
export type FieldRule = {
  id: string;
  entity: string;
  field_api_name: string;
  field_type: string;
  profile_version: number;
  target_column: string | null;
  status: FieldRuleStatus;
  proposed_by: "ai" | "admin";
  confidence: number | null;
  reason: string | null;
  decided_by: string | null;
  decided_at: string | null;
};

/** A field that goes to `custom` today, with the columns it may fill now. */
export type FieldMapField = {
  key: string;
  label: string;
  type: string;
  visible: boolean;
  targets: string[];
};

export type FieldMapTarget = {
  column: string;
  column_type: string;
  kind: "free" | "fixed";
  standard_field: string | null;
  open: boolean;
  reason: string | null;
};

/** `GET /crm/field-rules?entity=` */
export type FieldMap = {
  entity: string;
  profile_version: number | null;
  fields: FieldMapField[];
  rules: FieldRule[];
  targets: FieldMapTarget[];
};

/**
 * `POST /crm/field-rules/suggest` (CRM-Q7b). `asked` is false when the press
 * called no model. `dropped` counts the answers that the gateway refused.
 */
export type FieldSuggestion = {
  entity: string;
  profile_version: number | null;
  asked: boolean;
  dropped: number;
  /** The fields of the batches whose call failed or whose answer was bad. */
  unread: number;
  proposals: FieldRule[];
};

/** What an admin can do with the rule of one field. */
export type FieldAction = "approve" | "reject" | "pick";

export const FIELD_MAP_TITLE = "Field mapping";
export const FIELD_MAP_NOTE =
  "Pick the column that a Zoho field fills. A pick goes into a draft. After you approve the draft, the next sync applies it. The value also stays with the record.";
export const NO_FIELDS = "No Zoho field goes to custom.";
export const NO_TARGET = "No column can take this field.";
export const STALE_SENTENCE = "Zoho changed this field. Decide again.";
export const SUGGEST_LABEL = "Suggest mappings";
export const SUGGEST_FAILED = "The assistant could not suggest mappings.";

/**
 * The sentence after a press of "Suggest mappings" (CRM-Q7b). A proposal
 * waits for the admin, so the sentence asks for a decision and never says
 * that a field is mapped.
 */
export function suggestionLine(result: FieldSuggestion): string {
  const n = result.proposals.filter((rule) => rule.status === "proposed").length;
  if (n === 0) {
    return result.asked
      ? "The assistant found no field that fits a column."
      : "No proposal waits for you.";
  }
  const what = n === 1 ? "1 proposal waits" : `${n} proposals wait`;
  return `${what} for you. Approve or reject each one.`;
}

const STATUS_LABEL: Record<FieldRuleStatus, string> = {
  proposed: "Proposed",
  approved: "Approved",
  rejected: "Rejected",
  stale: "Stale",
};

const STATUS_TONE: Record<FieldRuleStatus, BadgeTone> = {
  proposed: "primary",
  approved: "success",
  rejected: "neutral",
  stale: "warning",
};

export function statusLabel(status: FieldRuleStatus): string {
  return STATUS_LABEL[status];
}

export function statusTone(status: FieldRuleStatus): BadgeTone {
  return STATUS_TONE[status];
}

/** A column as a person reads it: `no_of_employees` is "No of employees". */
export function columnLabel(column: string | null): string {
  return column ? humanizeKey(column) : "—";
}

/** The rule of one field, or `null` when no rule names it. */
export function ruleFor(map: FieldMap, key: string): FieldRule | null {
  return map.rules.find((rule) => rule.field_api_name === key) ?? null;
}

/**
 * The actions an admin has on a field. A field with no rule, or with a rule
 * that was rejected or went stale, can take a new target by hand. A proposal
 * waits for approve or reject. An approved rule can only be rejected, which
 * frees its column.
 */
export function actionsFor(rule: FieldRule | null, field: FieldMapField): FieldAction[] {
  const pick: FieldAction[] = field.targets.length > 0 ? ["pick"] : [];
  if (rule === null) return pick;
  switch (rule.status) {
    case "proposed":
      return ["approve", "reject"];
    case "approved":
      return ["reject"];
    case "rejected":
      return ["approve", ...pick];
    case "stale":
      return ["approve", "reject", ...pick];
  }
}

/** The legal columns of a field, as `SelectButton` options. */
export function pickOptions(field: FieldMapField): SelectOption[] {
  return field.targets.map((column) => ({ value: column, label: columnLabel(column) }));
}

/** "62% sure", for a proposal of the assistant. */
export function confidenceLabel(confidence: number | null): string | null {
  if (confidence === null || Number.isNaN(confidence)) return null;
  return `${Math.round(Math.min(Math.max(confidence, 0), 1) * 100)}% sure`;
}

/** The proposals and the stale rules: what waits for the admin. */
export function waitingCount(maps: Array<FieldMap | null>): number {
  return maps.reduce(
    (n, map) =>
      n + (map?.rules.filter((r) => r.status === "proposed" || r.status === "stale").length ?? 0),
    0,
  );
}

/** One line under a rule: who made it, and why. */
export function ruleLine(rule: FieldRule): string {
  const who = rule.proposed_by === "ai" ? "Proposed by the assistant" : "Picked by an admin";
  const sure = rule.proposed_by === "ai" ? confidenceLabel(rule.confidence) : null;
  return [who, sure, rule.reason].filter(Boolean).join(" · ");
}

// ── The plan: the versions of the Field map (CRM-Q18-2) ───────────────────

export type PlanState = "draft" | "approved" | "superseded";

/** One version in `GET /crm/mapping/plans`. It holds the count of its rules, not the rules. */
export type PlanSummary = {
  version: number;
  state: PlanState;
  rule_count: number;
  created_by: string;
  created_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  based_on: number | null;
};

/** `GET /crm/mapping/plans`: the version in use, and every version, newest first. */
export type PlanList = {
  approved: number | null;
  versions: PlanSummary[];
};

/**
 * One rule of a version. A `field` rule maps a Zoho field to a column. A
 * rule of another kind (a later ticket) passes through, and the card does
 * not draw it.
 */
export type PlanItem = {
  kind?: string;
  entity?: string;
  field_api_name?: string;
  field_type?: string;
  target_column?: string;
  profile_version?: number;
  [key: string]: unknown;
};

/** `GET /crm/mapping/plans/{version}`: one version with its rules. */
export type MappingPlan = Omit<PlanSummary, "rule_count"> & { rules: PlanItem[] };

/** One change of a draft (`POST /crm/mapping/plans`). */
export type PlanChange =
  | { op: "put"; entity: string; field_api_name: string; target_column: string }
  | { op: "remove"; entity: string; field_api_name: string };

/** The `field` rule of one item. */
export type FieldPlanRule = { entity: string; field: string; column: string };

/** One line of a draft against the version in use. */
export type PlanDiffRow =
  | { kind: "add"; entity: string; field: string; column: string }
  | { kind: "change"; entity: string; field: string; from: string; column: string }
  | { kind: "remove"; entity: string; field: string; from: string };

/** The stored entity word, as the card names it. The order is the order of the card. */
const PLAN_ENTITY_LABEL: Record<string, string> = {
  lead: "Leads",
  contact: "Contacts",
  company: "Companies",
  deal: "Deals",
};

export const PLAN_TITLE = "The field map in use";
export const PLAN_UNREAD = "The field map history did not load.";
export const DRAFT_LABEL = "Draft, not applied";
export const DRAFT_NOTE =
  "Nothing changes until you approve. The next sync then applies the draft.";
export const APPROVE_DRAFT_LABEL = "Approve draft";
export const GO_BACK_LABEL = "Go back";
export const HISTORY_LABEL = "History";
export const ADDED_TO_DRAFT = "Added to the draft. Approve the draft to apply it.";
export const NO_DRAFT_CHANGE = "That is the column in use now. The draft did not change.";
export const PLAN_MOVED =
  "The field map changed after your draft, so the draft was not applied. The card now shows the map in use. Make your change again.";
export const PLAN_BUSY =
  "The sync changed the field map at the same time. Nothing was applied. Try again in a moment.";
export const APPROVED_DONE = "Approved. The next sync applies the field map.";
export const WENT_BACK = "Done. The next sync applies the earlier field map.";
export const GO_BACK_TITLE = "Go back to the earlier field map?";

/** The `field` rule of a plan item, or `null` for an item of another kind. */
export function fieldRuleOf(item: PlanItem): FieldPlanRule | null {
  if ((item.kind ?? "field") !== "field") return null;
  const { entity, field_api_name: field, target_column: column } = item;
  if (typeof entity !== "string" || !entity) return null;
  if (typeof field !== "string" || !field) return null;
  if (typeof column !== "string" || !column) return null;
  return { entity, field, column };
}

function ruleKey(entity: string, field: string): string {
  return `${entity}\u0000${field}`;
}

function fieldRules(plan: MappingPlan | null): Map<string, FieldPlanRule> {
  const out = new Map<string, FieldPlanRule>();
  for (const item of plan?.rules ?? []) {
    const rule = fieldRuleOf(item);
    if (rule) out.set(ruleKey(rule.entity, rule.field), rule);
  }
  return out;
}

/** The field rules of a version, in the order of the card. */
export function planRules(plan: MappingPlan | null): FieldPlanRule[] {
  return [...fieldRules(plan).values()].sort(byCardOrder);
}

function entityRank(entity: string): number {
  const order = Object.keys(PLAN_ENTITY_LABEL);
  const at = order.indexOf(entity);
  return at < 0 ? order.length : at;
}

function byCardOrder(a: { entity: string; field: string }, b: { entity: string; field: string }) {
  return entityRank(a.entity) - entityRank(b.entity) || a.field.localeCompare(b.field);
}

/** The version in use, from the list. */
export function approvedSummary(list: PlanList | null): PlanSummary | null {
  if (!list || list.approved === null) return null;
  return list.versions.find((v) => v.version === list.approved) ?? null;
}

/**
 * The open draft: the newest draft whose base is the version in use. A draft
 * on an older base can never be approved (409 `plan_moved`), so the card
 * drops it.
 */
export function openDraft(list: PlanList | null): PlanSummary | null {
  if (!list || list.approved === null) return null;
  let best: PlanSummary | null = null;
  for (const v of list.versions) {
    if (v.state !== "draft" || v.based_on !== list.approved) continue;
    if (best === null || v.version > best.version) best = v;
  }
  return best;
}

function stamp(v: PlanSummary): number {
  const at = Date.parse(v.approved_at ?? v.created_at ?? "");
  return Number.isNaN(at) ? 0 : at;
}

/**
 * The version before the one in use: the version that was in use last. "Go
 * back" approves its content again. `null` when no version came before.
 */
export function previousVersion(list: PlanList | null): PlanSummary | null {
  if (!list) return null;
  let best: PlanSummary | null = null;
  for (const v of list.versions) {
    if (v.state !== "superseded") continue;
    if (best === null) {
      best = v;
      continue;
    }
    const a = stamp(best);
    const b = stamp(v);
    if (b > a || (b === a && v.version > best.version)) best = v;
  }
  return best;
}

/** What a draft changes against the version in use, in the order of the card. */
export function planDiff(approved: MappingPlan | null, draft: MappingPlan | null): PlanDiffRow[] {
  if (!draft) return [];
  const before = fieldRules(approved);
  const after = fieldRules(draft);
  const rows: PlanDiffRow[] = [];
  for (const [key, rule] of after) {
    const old = before.get(key);
    if (!old) {
      rows.push({ kind: "add", entity: rule.entity, field: rule.field, column: rule.column });
    } else if (old.column !== rule.column) {
      rows.push({
        kind: "change",
        entity: rule.entity,
        field: rule.field,
        from: old.column,
        column: rule.column,
      });
    }
  }
  for (const [key, rule] of before) {
    if (!after.has(key)) {
      rows.push({ kind: "remove", entity: rule.entity, field: rule.field, from: rule.column });
    }
  }
  return rows.sort(byCardOrder);
}

/**
 * The changes of the next draft. The gateway builds a new draft on the open
 * draft (Batch M review P1), so the draft keeps each earlier pick and each
 * suggestion, and the card sends only *next*. A change that the open draft
 * (or, with no draft, the map in use) already holds is no change, and an
 * empty list means that there is nothing to draft.
 */
export function nextDraftChanges(
  approved: MappingPlan | null,
  draft: MappingPlan | null,
  next: PlanChange,
): PlanChange[] {
  const base = draft ?? approved;
  const current = fieldRules(base).get(ruleKey(next.entity, next.field_api_name));
  const noChange =
    next.op === "put" ? current?.column === next.target_column : current === undefined;
  return noChange ? [] : [next];
}

// ── The items of a suggestion in a draft (Batch M review P1) ──────────────

/**
 * One item of a draft that is not a `field` rule: a module plays a core slot
 * (`role`), a field of it fills a column (`target`), or its values are a stage
 * (`stage`). The card draws each one, so an approve never applies an item
 * that the admin cannot see.
 */
export type KindDiffRow = { kind: "add" | "remove"; item: PlanItem };

/** The identity of an item of a kind: one role for each slot, and so on. */
function kindKey(item: PlanItem): string | null {
  switch (item.kind) {
    case "role":
      return `role\u0000${String(item.slot)}`;
    case "target":
      return `target\u0000${String(item.slot)}\u0000${String(item.module)}\u0000${String(item.target)}`;
    case "stage":
      return `stage\u0000${String(item.slot)}\u0000${String(item.module)}`;
    default:
      return null;
  }
}

/** The items of the kinds in a version, by identity, in a stable form. */
function kindItems(plan: MappingPlan | null): Map<string, PlanItem> {
  const out = new Map<string, PlanItem>();
  for (const item of plan?.rules ?? []) {
    const key = kindKey(item);
    if (key) out.set(key, item);
  }
  return out;
}

/** The text that tells two items of one identity apart. Counts do not count. */
function itemText(item: PlanItem): string {
  const { unmapped: _u, unsorted: _s, profile_version: _p, ...rest } = item;
  return JSON.stringify(rest, Object.keys(rest).sort());
}

const KIND_ORDER = ["role", "target", "stage"];

/** The items of the kinds that a draft adds or takes away, roles first. */
export function kindDiff(approved: MappingPlan | null, draft: MappingPlan | null): KindDiffRow[] {
  if (!draft) return [];
  const before = kindItems(approved);
  const after = kindItems(draft);
  const rows: KindDiffRow[] = [];
  for (const [key, item] of after) {
    const old = before.get(key);
    if (!old || itemText(old) !== itemText(item)) rows.push({ kind: "add", item });
  }
  for (const [key, item] of before) {
    if (!after.has(key)) rows.push({ kind: "remove", item });
  }
  return rows.sort(
    (a, b) =>
      KIND_ORDER.indexOf(String(a.item.kind)) - KIND_ORDER.indexOf(String(b.item.kind)) ||
      String(a.item.slot).localeCompare(String(b.item.slot)),
  );
}

/** A Zoho module or field as a person reads it: `Operator_Associates` is "Operator Associates". */
function sourceName(value: unknown): string {
  return typeof value === "string" && value ? humanizeKey(value) : "—";
}

/** The fields of a target item, in order: "Phone or Mobile". */
function fieldNames(value: unknown): string {
  const names = Array.isArray(value) ? value.map(sourceName) : [];
  return names.length > 0 ? names.join(" or ") : "—";
}

/** One item of a kind, as a person reads it. No engine word. */
export function kindLine(row: KindDiffRow): string {
  const { item } = row;
  const slot = entityLabel(String(item.slot ?? ""));
  let what: string;
  switch (item.kind) {
    case "role":
      what = `${sourceName(item.module)} shows as ${slot}`;
      break;
    case "target":
      what = `${slot} · ${columnLabel(typeof item.target === "string" ? item.target : null)} comes from ${fieldNames(item.fields)}`;
      break;
    default:
      what = `${slot} · Stage comes from ${sourceName(item.field_api_name)}`;
  }
  return row.kind === "add" ? `Add: ${what}` : `Remove: ${what}`;
}

function entityLabel(entity: string): string {
  return PLAN_ENTITY_LABEL[entity] ?? humanizeKey(entity);
}

/** A field rule as a person reads it: "Leads · Sector → Industry". */
export function planRuleLine(rule: FieldPlanRule): string {
  return `${entityLabel(rule.entity)} · ${humanizeKey(rule.field)} → ${columnLabel(rule.column)}`;
}

/** One line of the draft. */
export function diffLine(row: PlanDiffRow): string {
  const where = `${entityLabel(row.entity)} · ${humanizeKey(row.field)}`;
  switch (row.kind) {
    case "add":
      return `Add: ${where} → ${columnLabel(row.column)}`;
    case "change":
      return `Change: ${where} → ${columnLabel(row.column)}, not ${columnLabel(row.from)}`;
    case "remove":
      return `Remove: ${where} no longer fills ${columnLabel(row.from)}`;
  }
}

/** Who approved a version. The sync approves a version when Zoho changes a field. */
export function approverName(by: string | null): string {
  if (!by) return "an admin";
  return by === "system" ? "the sync, after a change in Zoho" : by;
}

/** "In use since 16 Oct, 10:00 am. Approved by a@x.test." */
export function approvedLine(v: PlanSummary): string {
  return `In use since ${dateTime(v.approved_at ?? v.created_at)}. Approved by ${approverName(v.approved_by)}.`;
}

/** How many rules a version holds. */
export function ruleCountLabel(n: number): string {
  return n === 1 ? "1 rule" : `${n} rules`;
}

/** The state of a version, in the words of the card. */
export function stateLabel(v: PlanSummary, draft: PlanSummary | null): string {
  switch (v.state) {
    case "approved":
      return "In use";
    case "superseded":
      return "Earlier";
    case "draft":
      return draft?.version === v.version ? DRAFT_LABEL : "Draft, not used";
  }
}

export function stateTone(v: PlanSummary, draft: PlanSummary | null): BadgeTone {
  if (v.state === "approved") return "success";
  if (v.state === "draft" && draft?.version === v.version) return "warning";
  return "neutral";
}

/** One row of the history: when, and who. */
export function historyLine(v: PlanSummary): string {
  if (v.state === "draft") return `Drafted ${dateTime(v.created_at)} by ${v.created_by}`;
  return `Approved ${dateTime(v.approved_at ?? v.created_at)} by ${approverName(v.approved_by)}`;
}

/** The body of the "Go back" question. It names the date of the earlier version. */
export function goBackBody(v: PlanSummary): string {
  return `The field map goes back to the one approved on ${dateTime(
    v.approved_at ?? v.created_at,
  )} (${ruleCountLabel(v.rule_count)}). The history keeps the map in use now, so you can go forward again. The next sync applies the change.`;
}

/** What a press of approve or "Go back" came to. */
export type PlanOutcome =
  | { kind: "done"; plan: MappingPlan }
  | { kind: "moved"; message: string }
  | { kind: "busy"; message: string }
  | { kind: "failed"; message: string };

function isConflict(err: unknown, code: string): boolean {
  return err instanceof CrmError && err.status === 409 && err.code === code;
}

/**
 * Run one write of the plan. A 409 `plan_busy` is a clash with the sync, and
 * the gateway wrote nothing, so the write runs once more after *waitMs*. A
 * second clash asks the admin to try again. A 409 `plan_moved` means that the
 * map changed after the draft: the caller reads the plan again and drops the
 * draft.
 */
export async function runPlanWrite(
  write: () => Promise<MappingPlan>,
  opts: { wait?: (ms: number) => Promise<void>; waitMs?: number } = {},
): Promise<PlanOutcome> {
  const wait = opts.wait ?? ((ms: number) => new Promise<void>((done) => setTimeout(done, ms)));
  for (let attempt = 0; ; attempt++) {
    try {
      return { kind: "done", plan: await write() };
    } catch (err) {
      if (isConflict(err, "plan_busy")) {
        if (attempt > 0) return { kind: "busy", message: PLAN_BUSY };
        await wait(opts.waitMs ?? 1000);
        continue;
      }
      if (isConflict(err, "plan_moved")) return { kind: "moved", message: PLAN_MOVED };
      const message =
        err instanceof CrmError && err.message ? err.message : "The change did not save.";
      return { kind: "failed", message };
    }
  }
}

/**
 * The calls of the plan that the card makes. The card passes the `api.ts`
 * calls, and a test passes a fake. `reread` reads the plan again.
 */
export type PlanCalls = {
  draft: (changes: PlanChange[]) => Promise<MappingPlan>;
  approve: (version: number) => Promise<MappingPlan>;
  restore: (version: number) => Promise<MappingPlan>;
  reread: () => unknown;
};

type WaitOpts = { wait?: (ms: number) => Promise<void>; waitMs?: number };

/**
 * A pick by hand: a new draft with the changes of the open draft and this
 * one. It changes no live rule. "unchanged" when the pick is the column in
 * use, and then no request goes out.
 */
export async function addToDraft(
  calls: PlanCalls,
  approved: MappingPlan | null,
  draft: MappingPlan | null,
  change: PlanChange,
): Promise<"drafted" | "unchanged"> {
  const changes = nextDraftChanges(approved, draft, change);
  if (changes.length === 0) return "unchanged";
  try {
    await calls.draft(changes);
  } finally {
    await calls.reread();
  }
  return "drafted";
}

/**
 * The one press that approves the open draft. The plan is read again after
 * each result. After a 409 `plan_moved` the open draft is gone from the new
 * read, because its base is no longer the version in use (`openDraft`).
 */
export async function approveDraft(
  calls: PlanCalls,
  version: number,
  opts: WaitOpts = {},
): Promise<PlanOutcome> {
  const outcome = await runPlanWrite(() => calls.approve(version), opts);
  await calls.reread();
  return outcome;
}

/** "Go back": approve the content of the version before, as a new version. */
export async function goBack(
  calls: PlanCalls,
  previous: PlanSummary,
  opts: WaitOpts = {},
): Promise<PlanOutcome> {
  const outcome = await runPlanWrite(() => calls.restore(previous.version), opts);
  await calls.reread();
  return outcome;
}
