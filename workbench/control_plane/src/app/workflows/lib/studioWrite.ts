/**
 * The rules of the Studio write form (WFC-4a-2a and WFC-4a-2b-1, `ai_studio.md` §14).
 *
 * A Studio write step adds one record to an app, or changes one record. The
 * step runs only when a schedule starts the workflow, and only after the owner
 * approves the card of the Human approval step above it. It runs as the OWNER
 * of the workflow, and the package must be one that the owner owns.
 *
 * A create holds `package_id`, `action` and `data`, and never a `record_id`:
 * publish ties no rule to the verb, so a create that holds a record id would
 * publish and then fail at run time, after the owner approved it. An update
 * holds `package_id`, `action`, `record_id` and `data`. The owner picks the
 * record from a list that the editor reads through a door of reads only, and
 * `data` holds only the fields that the owner added. No delete is offered.
 *
 * ⚠️ NO LEAK. The step stores the record id only. No title and no value of the
 * record goes into the config, the label or the summary of the node, and no
 * value of the record fills `data`.
 *
 * Every rule here is a pure function, so a test can drive each one. The form
 * (`StudioWriteForm.tsx`) only draws them. The gateway decides: the editor is
 * not a security boundary. Fences: `studioWrite.test.ts` and
 * `tests/unit/test_workflows_node_offer.py`.
 */

import { manyUnsupported } from "@/lib/studio/blocks";
import { sourceKey, type Row, type StudioDataDoor, type StudioSource } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import { checkForm, initialDrafts, type Drafts } from "@/lib/studio/formModel";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { fieldSpecs, formFields, recordTitle } from "@/lib/studio/packagePreview";
import {
  GONE_SENTENCE,
  NEEDS_CONNECTION_SENTENCE,
  NOT_DECLARED_SENTENCE,
  NOT_OPEN_SENTENCE,
  READ_ONLY_SENTENCE,
  SIGN_IN_SENTENCE,
  UNPUBLISHED_SENTENCE,
  readActions,
  verbMap,
  type DeclaredAction,
  type ObjectRow,
  type PackageDetail,
} from "@/lib/studio/realDoor";

import type { NodeType } from "./types";

/**
 * Every config key that the form writes: the keys of `graph.STUDIO_WRITE_KEYS`.
 * Only an update holds `record_id`.
 */
export const STUDIO_WRITE_KEYS = ["package_id", "action", "record_id", "data"] as const;
export type StudioWriteKey = (typeof STUDIO_WRITE_KEYS)[number];

/** The keys of a create step. A create never holds `record_id`. */
export const CREATE_KEYS = ["package_id", "action", "data"] as const;

/** The two verbs that the editor offers. A delete is never offered. */
export type WriteVerb = "create" | "update";
export const CREATE_VERB = "create";
export const UPDATE_VERB = "update";

/** The config of a Studio write that the palette drops: a create shape, with no action yet. */
export function newStudioWriteConfig(): Record<(typeof CREATE_KEYS)[number], unknown> {
  return { package_id: "", action: "", data: {} };
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/**
 * The keys of a config that hold a value. `patchSelected` in the editor merges
 * each patch, so a patch drops a key by setting it to `undefined`, and the save
 * (`JSON.stringify`) leaves that key out. So a key with `undefined` is absent.
 */
export function presentKeys(config: Record<string, unknown>): string[] {
  return Object.keys(config).filter((k) => config[k] !== undefined);
}

/** A record id as the store writes it: a UUID. */
const RECORD_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isRecordId = (v: unknown): v is string => typeof v === "string" && RECORD_ID.test(v);

// -- who may edit --------------------------------------------------------------

const normEmail = (value: unknown): string => (typeof value === "string" ? value.trim().toLowerCase() : "");

/**
 * The part of the member in the editor, for a Studio write.
 *
 * The step runs as the owner of the workflow, so only the owner may set it.
 * The test is the one of `crud._is_author`: both emails trimmed and lower-cased.
 * While the access of the member loads, nobody is the owner yet.
 */
export type WriterRole = "owner" | "loading" | "other";

export function writerRole(
  ownerEmail: string | null | undefined,
  accessEmail: string | null | undefined,
  accessLoading: boolean,
): WriterRole {
  if (accessLoading) return "loading";
  const owner = normEmail(ownerEmail);
  const me = normEmail(accessEmail);
  return owner !== "" && owner === me ? "owner" : "other";
}

export const NOT_OWNER_NOTE = "Only the owner of this workflow can set this step, because it runs as the owner.";
export const LOADING_ROLE_NOTE = "Checking who owns this workflow. The step stays read-only until then.";

// -- what the form says --------------------------------------------------------

export const SCHEDULE_NOTE = "This step runs only when a schedule starts the workflow, and only after the owner approves the card.";
export const TEST_RUN_NOTE = "A Test run stops at this step.";
export const VISIBLE_NOTE = "Each member who can open this workflow can read these values.";
export const STUDIO_WRITE_NOTES = [SCHEDULE_NOTE, TEST_RUN_NOTE, VISIBLE_NOTE] as const;

/** What the form says when a value holds `{{`. */
export const LITERAL_ONLY_MESSAGE =
  "A Studio write takes plain values only. It cannot read data from earlier steps, so remove the {{ marks.";

// -- the sources: package, action, data form -----------------------------------

/** One way to write a record: an object of the package, the verb, and the action. */
export interface WriteOffer {
  verb: WriteVerb;
  object: ObjectRow;
  /** The id of the declared action. It is the `config.action` of the step. */
  actionId: string;
}

/** Back compat: the create offer of WFC-4a-2a. */
export type CreateOffer = WriteOffer;

type OfferSource = Pick<PackageDetail, "access" | "actions" | "objects">;

/**
 * The writes that the editor offers for a package.
 *
 * A viewer answer offers none, because the owner of the workflow must own the
 * package. Each offer is a plain declared action (`verbMap` skips an action
 * with a `set`) of an object of the package. A create is `object:<name>.create`.
 * An update is `object:<name>.update`, and only for an object that also
 * declares a plain `object:<name>.list`, because the owner picks the record
 * from that list. A delete and a read are never offered.
 */
export function writeOffers(detail: OfferSource | null | undefined): WriteOffer[] {
  if (!detail || detail.access !== "owner") return [];
  const byUse = verbMap(detail.actions);
  const out: WriteOffer[] = [];
  for (const object of detail.objects ?? []) {
    if (!object || typeof object.name !== "string") continue;
    const create = byUse[`object:${object.name}.${CREATE_VERB}`];
    if (create) out.push({ verb: "create", object, actionId: create });
    const update = byUse[`object:${object.name}.${UPDATE_VERB}`];
    if (update && byUse[`object:${object.name}.list`]) out.push({ verb: "update", object, actionId: update });
  }
  return out;
}

export function createOffers(detail: OfferSource | null | undefined): WriteOffer[] {
  return writeOffers(detail).filter((o) => o.verb === "create");
}

export function updateOffers(detail: OfferSource | null | undefined): WriteOffer[] {
  return writeOffers(detail).filter((o) => o.verb === "update");
}

/** The plain update actions that the editor cannot offer, because their object declares no list. */
export function unlistedUpdates(detail: OfferSource | null | undefined): WriteOffer[] {
  if (!detail || detail.access !== "owner") return [];
  const byUse = verbMap(detail.actions);
  const out: WriteOffer[] = [];
  for (const object of detail.objects ?? []) {
    if (!object || typeof object.name !== "string") continue;
    const update = byUse[`object:${object.name}.${UPDATE_VERB}`];
    if (update && !byUse[`object:${object.name}.list`]) out.push({ verb: "update", object, actionId: update });
  }
  return out;
}

const nameOf = (object: Pick<ObjectRow, "name" | "label">): string => object.label || object.name;

function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The plain line for the updates that have no list, or null when there are none. */
export function unlistedLine(unlisted: readonly WriteOffer[]): string | null {
  if (unlisted.length === 0) return null;
  const names = unlisted.map((o) => nameOf(o.object));
  const whose = names.length === 1 ? "its" : "their";
  return `The editor cannot change a record of ${joinNames(names)}, because the package declares no action that lists ${whose} records.`;
}

/** What the package step says, from one read of the package. */
export type PackageView =
  | { kind: "viewer"; text: string }
  | { kind: "none"; text: string; unlisted: WriteOffer[] }
  | { kind: "ready"; offers: WriteOffer[]; unlisted: WriteOffer[] };

export function packageView(detail: OfferSource): PackageView {
  if (detail.access !== "owner") {
    return {
      kind: "viewer",
      text: "You can read this app, but you do not own it. Pick a package that you own.",
    };
  }
  const offers = writeOffers(detail);
  const unlisted = unlistedUpdates(detail);
  if (offers.length === 0) {
    return {
      kind: "none",
      text: "This package declares no action that adds or changes a record. Add one in Studio, then pick the package again.",
      unlisted,
    };
  }
  return { kind: "ready", offers, unlisted };
}

/** The words of an offer in the action picker. */
export function offerLabel(offer: WriteOffer): string {
  return `${offer.verb === "update" ? "Change" : "Add"} a ${nameOf(offer.object)}`;
}

/** The fields of the data form of a create: the fields of its Form block. */
export function dataFields(object: Pick<ObjectRow, "fields" | "rules">): FieldSpec[] {
  return formFields(fieldSpecs(object), computedKeys(object.rules));
}

/**
 * The fields that an update may change. A field that a rule fills is out (the
 * store refuses it), a lines relation is out (it holds no value), and a list
 * that has no control is out.
 */
export function updateFields(object: Pick<ObjectRow, "fields" | "rules">): FieldSpec[] {
  return dataFields(object).filter((f) => !manyUnsupported(f));
}

/** The fields of the form of one offer. */
export function fieldsFor(offer: WriteOffer): FieldSpec[] {
  return offer.verb === "update" ? updateFields(offer.object) : dataFields(offer.object);
}

export function dataDrafts(fields: readonly FieldSpec[], data: unknown): Drafts {
  return initialDrafts(fields, isRecord(data) ? data : undefined);
}

/** Every string inside a value, as `_flatten_strings` finds them. */
function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (isRecord(value)) return Object.values(value).flatMap(strings);
  return [];
}

const hasRef = (value: unknown): boolean => strings(value).some((s) => s.includes("{{"));

export type DataEdit =
  | { ok: true; patch: { data: Record<string, unknown>; record_id?: undefined } }
  | { ok: false; errors: Record<string, string> };

/**
 * The patch for one submit of the data form of a CREATE, or the refusals by field.
 *
 * `checkForm().value` is exactly the `data` that a create takes. A value that
 * `validateRecord` refuses writes nothing. A value with `{{` writes nothing,
 * because publish refuses it, and the write that the owner approves must be the
 * write that runs. The patch also drops a stray `record_id`, so a create that
 * the owner sets never holds one.
 */
export function dataEdit(fields: readonly FieldSpec[], drafts: Drafts): DataEdit {
  const checked = checkForm(fields, drafts);
  if (!checked.ok) return { ok: false, errors: checked.errors };
  const errors: Record<string, string> = {};
  for (const [key, value] of Object.entries(checked.value)) {
    if (hasRef(value)) errors[key] = LITERAL_ONLY_MESSAGE;
  }
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, patch: { data: checked.value, record_id: undefined } };
}

// -- the data of an update -----------------------------------------------------

/** The line of an update that changes no field yet. */
export const NO_FIELD_LINE = "This step changes no field yet. Add each field that it changes. The other fields keep their values.";
/** What a submit of an update with no added field says. It writes nothing. */
export const EMPTY_UPDATE_MESSAGE = "Add a field first. A change with no field writes nothing, so the step keeps its old values.";
/** What an added field with no value says. A step cannot clear a field. */
export const BLANK_FIELD_MESSAGE = "Give this field a value, or remove it. A step cannot clear a field.";

/** The added fields of a stored update: the keys of its `data`, in the order of the fields. */
export function initialAdded(fields: readonly FieldSpec[], data: unknown): string[] {
  const stored = isRecord(data) ? data : {};
  return fields.filter((f) => Object.hasOwn(stored, f.key)).map((f) => f.key);
}

/** Add one field, in the order of the fields. An unknown key or a key already there changes nothing. */
export function addField(fields: readonly FieldSpec[], added: readonly string[], key: string): string[] {
  const next = new Set([...added, key]);
  return fields.filter((f) => next.has(f.key)).map((f) => f.key);
}

export function removeField(added: readonly string[], key: string): string[] {
  return added.filter((k) => k !== key);
}

/** The fields that the owner can still add. */
export function addableFields(fields: readonly FieldSpec[], added: readonly string[]): FieldSpec[] {
  const have = new Set(added);
  return fields.filter((f) => !have.has(f.key));
}

export type UpdateEdit =
  | { ok: true; patch: { data: Record<string, unknown> } }
  | { ok: false; errors: Record<string, string>; message: string | null };

/**
 * The patch for one submit of the data form of an UPDATE, or the refusals.
 *
 * ⚠️ Only the ADDED fields go into `data`. An update is partial: the store
 * merges `data` into the record. `checkForm` over every field would draft an
 * untouched checkbox to `false` and write it, and would refuse a blank required
 * field that the owner never meant to change. So each added field is checked
 * alone. An added field must hold a value (no clear), and no value holds `{{`.
 */
export function updateEdit(fields: readonly FieldSpec[], added: readonly string[], drafts: Drafts): UpdateEdit {
  const keep = new Set(added);
  const shown = fields.filter((f) => keep.has(f.key));
  if (shown.length === 0) return { ok: false, errors: {}, message: EMPTY_UPDATE_MESSAGE };
  const value: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const f of shown) {
    const one = checkForm([f], drafts);
    if (!one.ok) {
      errors[f.key] = one.errors[f.key] ?? Object.values(one.errors)[0] ?? BLANK_FIELD_MESSAGE;
    } else if (!Object.hasOwn(one.value, f.key)) {
      errors[f.key] = BLANK_FIELD_MESSAGE;
    } else if (hasRef(one.value[f.key])) {
      errors[f.key] = LITERAL_ONLY_MESSAGE;
    } else {
      value[f.key] = one.value[f.key];
    }
  }
  if (Object.keys(errors).length > 0) return { ok: false, errors, message: null };
  return { ok: true, patch: { data: value } };
}

// -- the patches ---------------------------------------------------------------

/**
 * The patch for a pick of a package: a new package clears the action, the
 * record and the values. `record_id` is `undefined`, never null or "", because
 * publish refuses both, and the save leaves an `undefined` key out.
 */
export function packagePatch(packageId: string): {
  package_id: string; action: string; record_id: undefined; data: Record<string, unknown>;
} {
  return { package_id: packageId, action: "", record_id: undefined, data: {} };
}

/** The patch for a pick of an action: a new action clears the record and the values. */
export function actionPatch(actionId: string): { action: string; record_id: undefined; data: Record<string, unknown> } {
  return { action: actionId, record_id: undefined, data: {} };
}

/** The patch for a pick of a record: the id, and nothing about the record. */
export function recordPatch(recordId: string): { record_id: string } {
  return { record_id: recordId };
}

// -- the record picker ---------------------------------------------------------

/** One page of the picker: `PAGE_MAX` of `acb_common.paging`. */
export const RECORD_PAGE = 100;
/** The last offset that the store reads: `MAX_OFFSET` of `acb_common.studio_query`. */
export const MAX_RECORD_OFFSET = 10_000;

export function recordListSource(objectName: string, offset: number): StudioSource {
  return { action: `object:${objectName}.list`, args: { limit: RECORD_PAGE, offset } };
}

export function recordGetSource(objectName: string, recordId: string): StudioSource {
  return { action: `object:${objectName}.get`, args: { id: recordId } };
}

/** The offset of the next page, or null when the last page was not full or the store reads no further. */
export function nextOffset(offset: number, pageLength: number): number | null {
  if (pageLength < RECORD_PAGE) return null;
  const next = offset + RECORD_PAGE;
  return next <= MAX_RECORD_OFFSET ? next : null;
}

/**
 * The cache key of one read of the picker, or null. ⚠️ Only the owner reads.
 * A member who is not the owner, and the owner while access loads, start no
 * read: the key is null, so the hook holds still.
 */
export function recordReadKey(
  role: WriterRole,
  door: StudioDataDoor | null,
  source: StudioSource | null,
): string | null {
  return role === "owner" && door && source ? sourceKey(door, source) : null;
}

/** Does the package declare a plain read of one record of the object? */
export function canGetRecord(actions: readonly DeclaredAction[] | undefined, objectName: string): boolean {
  return !!verbMap(readActions(actions))[`object:${objectName}.get`];
}

export interface RecordOption {
  value: string;
  label: string;
  hint?: string;
}

/**
 * The options of the picker: one for each loaded row, named by `recordTitle`.
 * A saved record that the rows do not hold comes first, named by its title from
 * the one get read, or by its id.
 */
export function recordOptions(
  fields: readonly FieldSpec[],
  rows: readonly Row[],
  recordId: string,
  savedTitle: string | null,
): RecordOption[] {
  const out: RecordOption[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const id = String(row.id ?? "");
    if (!id || seen.has(id)) continue;
    seen.add(id);
    // The last marks of the id tell two records with one title apart. A
    // time-ordered id shares its first marks, so the hint takes the end.
    out.push({ value: id, label: recordTitle(fields, row), hint: id.slice(-8) });
  }
  if (recordId && !seen.has(recordId)) out.unshift({ value: recordId, label: savedTitle || recordId });
  return out;
}

/** What the saved record of the step is, from its get read. */
export type SavedRecord = "none" | "loading" | "found" | "gone" | "failed";

export function savedRecordState(read: { key: string | null; data: Row[] | undefined; error: string | null }): SavedRecord {
  if (!read.key) return "none";
  if (read.error === GONE_SENTENCE) return "gone";
  if (read.data && read.data.length > 0) return "found";
  if (read.error) return "failed";
  return "loading";
}

export const GONE_RECORD_MESSAGE = "The record of this step is gone. Pick another record.";
export const SAVED_FAILED_MESSAGE = "Could not read the record of this step.";
export const RECORDS_FAILED_MESSAGE = "Could not load the records. Check the connection, then try again.";
export const NO_RECORDS_LINE = "This object has no records yet. Add one in the app, then pick it here.";
export const PICK_RECORD_NOTE = "Pick the record that this step changes. Without a record, the step fails when it runs.";
export const STRAY_RECORD_NOTE = "This step adds a record, so it takes no record id. Use these values to clear it.";

/** The door sentences that tell the owner what to do. Any other failure gets one plain sentence. */
const DOOR_SENTENCES: ReadonlySet<string> = new Set([
  NOT_DECLARED_SENTENCE, UNPUBLISHED_SENTENCE, SIGN_IN_SENTENCE, NOT_OPEN_SENTENCE,
  READ_ONLY_SENTENCE, NEEDS_CONNECTION_SENTENCE,
]);

/** The words of a failed read of the records. Never the raw text of an error. */
export function recordsFailedText(error: string): string {
  if (DOOR_SENTENCES.has(error) || error.startsWith("This app is busy now.")) {
    return `Could not load the records. ${error}`;
  }
  return RECORDS_FAILED_MESSAGE;
}

/** The note of a step whose record id does not fit its verb, or null. */
export function stepNote(verb: WriteVerb | undefined, config: Record<string, unknown>): string | null {
  if (verb === "update" && !isRecordId(config.record_id)) return PICK_RECORD_NOTE;
  if (verb === "create" && config.record_id !== undefined) return STRAY_RECORD_NOTE;
  return null;
}

// -- a stored step that the form cannot edit -----------------------------------

const textOf = (config: Record<string, unknown>, key: string): string =>
  typeof config[key] === "string" ? (config[key] as string) : "";

/**
 * Why the form cannot edit a stored step, or `null` when it can.
 *
 * `detail` is `undefined` while the read is on its way, and `null` when the
 * package could not be read as an owner. `known` is whether the package is in
 * the list of the owner (`undefined` while the list loads). `fields` are the
 * fields of the form of the step's offer (`fieldsFor`).
 */
export function uneditableReason(
  config: Record<string, unknown>,
  known: boolean | undefined,
  detail: PackageView | null | undefined,
  fields: readonly FieldSpec[] | null,
): string | null {
  const extra = presentKeys(config).filter((k) => !(STUDIO_WRITE_KEYS as readonly string[]).includes(k));
  if (extra.length > 0) {
    return "This step holds a value that the editor does not know, so the editor cannot change it.";
  }
  const pkg = textOf(config, "package_id");
  const action = textOf(config, "action");
  if (pkg === "") return null;
  if (known === false) return "This package is not one that you own, so the editor cannot change this step.";
  if (detail === null) return "The editor could not read this package, so it cannot change this step.";
  if (detail && detail.kind !== "ready") return detail.text;
  if (detail && action !== "" && !detail.offers.some((o) => o.actionId === action)) {
    if (detail.unlisted.some((o) => o.actionId === action)) {
      return "This step changes a record of an object that has no list action, so the editor cannot pick its record.";
    }
    return "This step uses an action that the editor does not offer, so the editor cannot change it.";
  }
  if (fields && isRecord(config.data)) {
    const keys = new Set(fields.map((f) => f.key));
    if (Object.keys(config.data).some((k) => !keys.has(k))) {
      return "This step holds values that the form has no field for, so the editor cannot change it.";
    }
  }
  return null;
}

/** One line of the read-only summary of a stored step. */
export interface SummaryLine {
  label: string;
  value: string;
}

const clip = (text: string, max = 80): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

/** The summary of a stored step, in words. It holds no input, and the record shows by its id only. */
export function storedSummary(config: Record<string, unknown>, packageName?: string): SummaryLine[] {
  const lines: SummaryLine[] = [];
  const pkg = textOf(config, "package_id");
  lines.push({ label: "Package", value: packageName || pkg || "Not set" });
  lines.push({ label: "Action", value: textOf(config, "action") || "Not set" });
  if (typeof config.record_id === "string") lines.push({ label: "Record", value: config.record_id });
  const data = isRecord(config.data) ? config.data : {};
  for (const [key, value] of Object.entries(data)) {
    lines.push({ label: key, value: clip(typeof value === "string" ? value : JSON.stringify(value)) });
  }
  return lines;
}

/**
 * The words of the card of a Studio write on the canvas. They come from the
 * config alone: the action id, and whether the step names a record. They never
 * name the record or a value.
 */
export function studioWriteSummary(config: Record<string, unknown>): {
  summary: string; detail: string; badges: string[];
} {
  const action = typeof config.action === "string" ? config.action.trim() : "";
  const changes = typeof config.record_id === "string" && config.record_id !== "";
  return {
    summary: !action
      ? "No action picked yet."
      : changes
        ? "Changes a record after the owner approves it."
        : "Adds a record after the owner approves it.",
    detail: action,
    badges: ["needs approval"],
  };
}

// -- the pair: approval, then write --------------------------------------------

/** What the palette drops: a node, and the nodes that follow it in a chain. */
export interface ChainDrop {
  nodeType: NodeType;
  label: string;
  config: Record<string, unknown>;
  then?: ChainDrop[];
}

/** The Human approval that clears one write, and the write that follows it. */
export function studioWritePair(): ChainDrop {
  return {
    nodeType: "approval",
    label: "Approve the record",
    config: { message: "Add this record to the app?" },
    then: [{ nodeType: "studio_write", label: "Add a record", config: newStudioWriteConfig() }],
  };
}

/**
 * The same pair for a change (WFC-4a-2b-1). The write has no action yet, so the
 * owner picks the change action and the record in the form. The words are
 * fixed, and name no record.
 */
export function studioWriteChangePair(): ChainDrop {
  return {
    nodeType: "approval",
    label: "Approve the change",
    config: { message: "Change this record in the app?" },
    then: [{ nodeType: "studio_write", label: "Change a record", config: newStudioWriteConfig() }],
  };
}

/** The drops of a chain in graph order. */
export function chainOf(drop: ChainDrop): ChainDrop[] {
  return [drop, ...(drop.then ?? [])];
}

// -- the shape check (the twin of `_validate_studio_writes`) -------------------

export interface ShapeNode {
  id: string;
  type: string;
}
export interface ShapeEdge {
  source: string;
  target: string;
  sourceHandle?: string | null;
}

export interface ShapeIssue {
  /** The name of the engine issue. */
  name: "studio_write_without_approval" | "approval_clears_one_write";
  message: string;
}

/**
 * The shape issues of one Studio write node, as the engine finds them.
 *
 * It mirrors `graph._validate_studio_writes`: the write has ONE parent, and the
 * parent is an approval. One approval clears one write, so the first write of
 * an approval (in the sorted order of the edges) passes and each other fails.
 */
export function studioWriteShape(
  nodeId: string,
  nodes: readonly ShapeNode[],
  edges: readonly ShapeEdge[],
): ShapeIssue[] {
  const typeOf = new Map(nodes.map((n) => [n.id, n.type]));
  if (typeOf.get(nodeId) !== "studio_write") return [];
  const keys = new Map<string, [string, string, string]>();
  for (const e of edges) {
    if (!typeOf.has(e.source) || !typeOf.has(e.target)) continue;
    const key: [string, string, string] = [e.source, e.target, e.sourceHandle ? String(e.sourceHandle) : ""];
    keys.set(key.join("\u0000"), key);
  }
  const sorted = [...keys.values()].sort((a, b) =>
    a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0,
  );
  const above = sorted.filter(([, tgt]) => tgt === nodeId).map(([src]) => src);
  const issues: ShapeIssue[] = [];
  if (above.length !== 1 || typeOf.get(above[0]) !== "approval") {
    issues.push({
      name: "studio_write_without_approval",
      message: "A Studio write must follow a Human approval step directly.",
    });
  }
  for (const gate of new Set(above)) {
    if (typeOf.get(gate) !== "approval") continue;
    const writes = sorted.filter(([src, tgt]) => src === gate && typeOf.get(tgt) === "studio_write").map(([, tgt]) => tgt);
    if (writes.length > 1 && writes.slice(1).includes(nodeId)) {
      issues.push({
        name: "approval_clears_one_write",
        message: "One Human approval clears one Studio write only. Add a second approval step.",
      });
    }
  }
  return issues;
}
