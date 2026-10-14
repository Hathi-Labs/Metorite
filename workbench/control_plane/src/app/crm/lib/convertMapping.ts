// ── The lead-conversion mapping, Native mode (WS-53 CRM-C6a) ───────────────
//
// Spec: project-docs/specs/crm_platform.md section 20.9, "CRM-C6a". The owner
// asked for fields and mappings between the CRM modules "like it is there on
// ZOHO CRM". In Zoho an admin sets which lead field fills which Contact,
// Account and Deal field at conversion. This is that table.
//
// Pure, except for the two calls at the end, so the vitest setup (no DOM) can
// test every rule. `components/ConvertMappingCard.tsx` draws what this file
// decides.
//
// ⚠️ The gateway is the authority (`routes/crm/convert_mapping.py`). It sends
// the compatible targets of each lead field in `choices`, so this file never
// copies the type rule. Each call goes through `call` of `api.ts`, the one way
// to call the gateway (CRM-C4p item 5).

import { call } from "./api";

/** A target entity of the map, as the gateway names it. */
export type MapEntity = "contact" | "company" | "deal";

/** Why a cell has its target. */
export type MapReason = "auto" | "skip" | "explicit";

/** The target of one lead field in one entity (`MappingCell`). */
export type MappingCell = {
  target_key: string | null;
  reason: MapReason;
  /** Why an explicit target takes no value now, or null. */
  problem: string | null;
  /** The keys of the live target fields that the lead field can fill. */
  choices: string[];
};

export type LeadFieldMapping = {
  key: string;
  label: string;
  type: string;
  mapping: Record<MapEntity, MappingCell>;
};

export type TargetField = { key: string; label: string; type: string; retired: boolean };

/** `GET /crm/conversion-mapping`. */
export type ConversionMapping = {
  fields: LeadFieldMapping[];
  targets: Record<MapEntity, TargetField[]>;
};

/** One row of `PUT /crm/conversion-mapping`. No target and no skip is "auto". */
export type MappingRow = {
  source_key: string;
  target_entity: MapEntity;
  target_key?: string;
  skip?: boolean;
};

/** The three tables of the section, in the order of the screen. */
export const MAP_ENTITIES: readonly { entity: MapEntity; label: string }[] = [
  { entity: "contact", label: "Contact" },
  { entity: "company", label: "Company" },
  { entity: "deal", label: "Deal" },
];

/** The choice value of "auto" and of "skip". A field key never starts with `_`. */
export const AUTO = "_auto";
export const SKIP = "_skip";

/** The note of the section. */
export const CONVERT_MAPPING_NOTE =
  "When a lead converts, each custom lead field fills the field that you choose here. " +
  "Auto fills the field with the same key. The fixed fields keep their own mapping.";

/** What the card says when the org has no custom lead field. */
export const NO_LEAD_FIELDS = "Add a custom lead field in Fields, then map it here.";

/** The words of a failed read. */
export const MAPPING_READ_FAILED = "The conversion mapping could not be read.";

/** The words of each `problem` of an explicit target. */
export const PROBLEM_TEXT: Record<string, string> = {
  target_missing: "The target field does not exist.",
  target_retired: "The target field is retired, so it takes no value.",
  type_mismatch: "The target field type cannot take this value now.",
};

/** The short word of each `problem`, for the disabled option. */
const PROBLEM_HINT: Record<string, string> = {
  target_missing: "missing",
  target_retired: "retired",
  type_mismatch: "wrong type",
};

/** The choice that a cell shows. */
export function choiceOf(cell: MappingCell): string {
  if (cell.reason === "skip") return SKIP;
  if (cell.reason === "explicit" && cell.target_key) return cell.target_key;
  return AUTO;
}

/** The label of a target field, or its key when the list does not hold it. */
function targetLabel(targets: readonly TargetField[], key: string): string {
  return targets.find((t) => t.key === key)?.label ?? key;
}

export type ChoiceOption = { value: string; label: string; hint?: string; disabled?: boolean };

/**
 * The options of one cell: Auto, Skip, then each compatible target field.
 *
 * Auto names the field it fills now, or says that none matches. An explicit
 * target that is no longer a choice (retired, gone, or of another type) stays
 * in the list, disabled, so the button still names it.
 */
export function choiceOptions(cell: MappingCell, targets: readonly TargetField[]): ChoiceOption[] {
  const auto =
    cell.reason === "auto" && cell.target_key
      ? `Auto: ${targetLabel(targets, cell.target_key)}`
      : "Auto";
  const out: ChoiceOption[] = [
    { value: AUTO, label: auto, hint: cell.reason === "auto" && !cell.target_key ? "no match" : undefined },
    { value: SKIP, label: "Skip" },
    ...cell.choices.map((key) => ({ value: key, label: targetLabel(targets, key) })),
  ];
  if (cell.reason === "explicit" && cell.target_key && !cell.choices.includes(cell.target_key)) {
    out.push({
      value: cell.target_key,
      label: targetLabel(targets, cell.target_key),
      hint: cell.problem ? (PROBLEM_HINT[cell.problem] ?? cell.problem) : undefined,
      disabled: true,
    });
  }
  return out;
}

/** The row of one cell, or null for "auto", which needs no row. */
function rowOf(source: string, entity: MapEntity, choice: string): MappingRow | null {
  if (choice === AUTO) return null;
  if (choice === SKIP) return { source_key: source, target_entity: entity, skip: true };
  return { source_key: source, target_entity: entity, target_key: choice };
}

/**
 * The whole map for `PUT`, with one cell changed.
 *
 * The route writes the whole map, so each other cell keeps its choice. An
 * "auto" cell sends no row, because no row means "auto".
 */
export function rowsWith(
  served: ConversionMapping,
  change: { source: string; entity: MapEntity; choice: string },
): MappingRow[] {
  const rows: MappingRow[] = [];
  for (const field of served.fields) {
    for (const { entity } of MAP_ENTITIES) {
      const choice =
        field.key === change.source && entity === change.entity
          ? change.choice
          : choiceOf(field.mapping[entity]);
      const row = rowOf(field.key, entity, choice);
      if (row) rows.push(row);
    }
  }
  return rows;
}

/** The lead fields of one target table: each lead field with its cell. */
export function tableRows(
  served: ConversionMapping,
  entity: MapEntity,
): { field: LeadFieldMapping; cell: MappingCell }[] {
  return served.fields.map((field) => ({ field, cell: field.mapping[entity] }));
}

// ── The calls ───────────────────────────────────────────────────────────────

export const CONVERT_MAPPING_KEY = "crm:conversion-mapping";

export function getConversionMapping(): Promise<ConversionMapping> {
  return call<ConversionMapping>("/conversion-mapping");
}

export function putConversionMapping(rows: MappingRow[]): Promise<ConversionMapping> {
  return call<ConversionMapping>("/conversion-mapping", {
    method: "PUT",
    body: JSON.stringify({ rows }),
  });
}
