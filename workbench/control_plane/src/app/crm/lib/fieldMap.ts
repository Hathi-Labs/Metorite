// ── The Field map: which Zoho field fills which column (WS-53 CRM-Q7a) ─────
//
// Pure, so the vitest setup (no DOM) can test every rule. `FieldMapCard.tsx`
// draws what this file decides. The gateway serves the map at
// `GET /crm/field-rules?entity=`, and it decides what is legal: each field
// carries the columns it may fill (`targets`). This file holds no copy of the
// type or the layout rules.
//
// Spec: project-docs/specs/crm_platform.md section 18.7, "CRM-Q7a".

import type { BadgeTone } from "@/components/ui/Badge";
import type { SelectOption } from "@/components/ui/SelectButton";
import { humanizeKey } from "./connect";

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
  "Pick the column that a Zoho field fills. The next sync applies the choice. The value also stays with the record.";
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
