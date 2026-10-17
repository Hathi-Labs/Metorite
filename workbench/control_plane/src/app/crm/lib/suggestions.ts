// ── The suggestions of the mapping (WS-53 CRM-Q20-2) ──────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 18.9.6, "CRM-Q20-2". The
// mapping pipeline suggests a Zoho module for a core list that is empty, for
// example "Commercials" for Deals. The admin sees real records first, uses
// it in one press, and can undo it. The list of the slot then opens the
// module's own list (option A): no record moves.
//
// Pure, so the test holds every word and rule without a browser. The words
// carry no engine term and no confidence number (section 18.9.2): no "draft",
// no "version", no "role", no "slot", no "overlay", no percent.

import type { CrmModeBody } from "./api";
import { humanizeKey } from "./connect";
import type { EntitySlug } from "./types";

/** A core list, as the gateway's items name it. */
export type SlotWord = "company" | "contact" | "lead" | "deal";

export const SLOT_SLUG: Record<SlotWord, EntitySlug> = {
  company: "organizations",
  contact: "contacts",
  lead: "leads",
  deal: "deals",
};

/** The gateway's word of a list, or `null` for a word it does not know. */
export function slotOf(slug: string): SlotWord | null {
  const found = (Object.keys(SLOT_SLUG) as SlotWord[]).find((s) => SLOT_SLUG[s] === slug);
  return found ?? null;
}

export type SuggestionTarget = {
  target: string;
  fields: string[];
  labels: string[];
  unmapped: number | null;
};

export type SuggestionStage = {
  field: string;
  label: string;
  values: Record<string, string>;
  unsorted: number | null;
};

/** `GET /crm/mapping/suggestions`: one suggestion for an empty list. */
export type Suggestion = {
  slot: SlotWord;
  version: number;
  module: string;
  label: string;
  records: number;
  targets: SuggestionTarget[];
  stage: SuggestionStage | null;
};

/** A list that a Zoho module shows now. */
export type OverlayInUse = { slot: SlotWord; module: string; label: string };

export type SuggestionList = { suggestions: Suggestion[]; overlays: OverlayInUse[] };

/** `POST /crm/mapping/preview` with a slot: real records of the module. */
export type OverlayPreview = {
  slot: SlotWord;
  module: string;
  columns: string[];
  rows: Array<{ id: string; values: Record<string, unknown> }>;
  total: number;
  unmapped: Record<string, number>;
  unsorted: number;
};

export type SuggestionDone = { slot: SlotWord; module: string; version: number };

/**
 * The module that each list opens, by API slug, from the `overlays` of
 * `GET /crm/mode`. A word that the client does not know, or a value that is
 * not a name, is left out, so a bad body never points a rail row anywhere.
 */
export function overlaysBySlug(
  overlays: CrmModeBody["overlays"] | undefined,
): Partial<Record<EntitySlug, string>> {
  const out: Partial<Record<EntitySlug, string>> = {};
  if (!overlays || typeof overlays !== "object") return out;
  for (const [word, module] of Object.entries(overlays)) {
    const slot = word in SLOT_SLUG ? (word as SlotWord) : null;
    if (slot && typeof module === "string" && module) out[SLOT_SLUG[slot]] = module;
  }
  return out;
}

/** The suggestion for one list, or `null`. */
export function suggestionFor(list: SuggestionList | null, slug: EntitySlug): Suggestion | null {
  return list?.suggestions.find((s) => SLOT_SLUG[s.slot] === slug) ?? null;
}

// ── The words ──────────────────────────────────────────────────────────────

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;
}

/** A list of names as a sentence: "A, B and C". */
function andList(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** A column of the list as a person reads it: `expected_close_date` is "Expected close date". */
export function columnName(column: string): string {
  return column === "stage" ? "Stage" : humanizeKey(column);
}

/** The words of one Zoho field, or of two fields with "or" between. */
function fieldWords(t: SuggestionTarget): string {
  const names = t.labels.length > 0 ? t.labels : t.fields.map(humanizeKey);
  return names.join(" or ");
}

/** The button of the empty list: "Preview Commercials as Deals". */
export function previewLabel(s: Suggestion, listLabel: string): string {
  return `Preview ${s.label} as ${listLabel}`;
}

/** The line in the empty list, for an admin. */
export function emptyLine(s: Suggestion, listLabel: string): string {
  return `Your Zoho keeps ${listLabel.toLowerCase()} in ${s.label}, with ${plural(s.records, "record", "records")}.`;
}

/** The line in an empty list for a member, who sees no suggestion. */
export function memberEmptyLine(listLabel: string): string {
  return `Your Zoho sent no ${listLabel.toLowerCase()} to this CRM yet.`;
}

/** The three facts of the panel. */
export function facts(s: Suggestion, listLabel: string): string[] {
  const columns = s.targets.map((t) => columnName(t.target));
  const fields =
    columns.length > 0
      ? `${plural(columns.length, "column comes", "columns come")} from its fields: ${andList(columns)}.`
      : `No column of ${listLabel} comes from its fields yet.`;
  let stage = `It has no stage, so its records show with no stage.`;
  if (s.stage) {
    const words = Object.values(s.stage.values);
    const count = (w: string) => words.filter((v) => v === w).length;
    stage = `The stage comes from ${s.stage.label}: ${count("won")} won, ${count("lost")} lost and ${count("open")} open.`;
  }
  return [`${s.label} holds ${plural(s.records, "record", "records")}.`, fields, stage];
}

/** At most three checks: what did not fit, from the counts of the sample. */
export function checks(s: Suggestion, preview: OverlayPreview | null): string[] {
  const out: string[] = [];
  for (const t of s.targets) {
    const n = preview?.unmapped[t.target] ?? t.unmapped ?? 0;
    if (n > 0) {
      out.push(`${plural(n, "value", "values")} of ${fieldWords(t)} did not fit the ${columnName(t.target)} column.`);
    }
  }
  const unsorted = preview?.unsorted ?? s.stage?.unsorted ?? 0;
  if (unsorted > 0) {
    out.push(`${plural(unsorted, "record has", "records have")} a stage that is not sorted. It shows with no stage.`);
  }
  if (!s.targets.some((t) => t.target === "name")) {
    out.push("No field gives the name, so each record shows its Zoho name.");
  }
  return out.slice(0, 3);
}

/** What changes, and what does not. */
export function whatChanges(s: Suggestion, listLabel: string): string[] {
  return [
    `${listLabel} opens the list of ${s.label}.`,
    "No record moves, and your Zoho does not change.",
    "You can undo it at any time in CRM settings, under Suggestions.",
  ];
}

/** The one action of the panel: "Use Commercials as Deals". */
export function useLabel(s: Suggestion, listLabel: string): string {
  return `Use ${s.label} as ${listLabel}`;
}

export const DISMISS_LABEL = "Dismiss";
export const UNDO_LABEL = "Undo";
export const SUGGESTIONS_TITLE = "Suggestions";
export const SUGGESTIONS_NOTE =
  "Metorite reads your Zoho and suggests where an empty list can find its records. Nothing changes until you choose.";
export const NO_SUGGESTIONS = "No suggestion waits for you.";
export const PREVIEW_UNREAD = "The records did not load. Try again.";

/** The toast after a use. It carries Undo. */
export function usedLine(s: { label: string }, listLabel: string): string {
  return `${listLabel} now shows ${s.label}.`;
}

export function undoneLine(listLabel: string): string {
  return `${listLabel} shows its own list again.`;
}

export function dismissedLine(s: Suggestion, listLabel: string): string {
  return `Metorite will not suggest ${s.label} for ${listLabel} again, unless your Zoho changes.`;
}

/** The line of a list in use, in CRM settings. */
export function inUseLine(o: OverlayInUse, listLabel: string): string {
  return `${listLabel} shows the records of ${o.label}.`;
}

/** One cell of the preview, as text. */
export function cellText(column: string, value: unknown): string {
  if (column === "stage") {
    if (value === "won") return "Won";
    if (value === "lost") return "Lost";
    if (value === "open") return "Open";
    return "Not sorted";
  }
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** The rows that the panel draws: a few, so the panel stays short. */
export const PREVIEW_SHOWN = 8;
