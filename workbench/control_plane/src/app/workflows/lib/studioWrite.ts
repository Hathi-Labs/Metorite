/**
 * The rules of the Studio write form (WFC-4a-2a, `ai_studio.md` §14).
 *
 * A Studio write step adds one record to an app. The step runs only when a
 * schedule starts the workflow, and only after the owner approves the card of
 * the Human approval step above it. It runs as the OWNER of the workflow, and
 * the package must be one that the owner owns. This slice offers CREATE only:
 * there is no record id, no update and no delete.
 *
 * Every rule here is a pure function, so a test can drive each one. The form
 * (`StudioWriteForm.tsx`) only draws them. The gateway decides: the editor is
 * not a security boundary. Fences: `studioWrite.test.ts` and
 * `tests/unit/test_workflows_node_offer.py`.
 */

import type { FieldSpec } from "@/lib/studio/fieldCheck";
import { checkForm, initialDrafts, type Drafts } from "@/lib/studio/formModel";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { fieldSpecs, formFields } from "@/lib/studio/packagePreview";
import { verbMap, type ObjectRow, type PackageDetail } from "@/lib/studio/realDoor";

import type { NodeType } from "./types";

/**
 * The config keys of a Studio write that the form writes, and no other. The
 * engine also takes `record_id` (`graph.STUDIO_WRITE_KEYS`), which belongs to
 * an update, and an update is not in this slice.
 */
export const STUDIO_WRITE_KEYS = ["package_id", "action", "data"] as const;
export type StudioWriteKey = (typeof STUDIO_WRITE_KEYS)[number];

/** The only verb of this slice. */
export const CREATE_VERB = "create";

/** The config of a Studio write that the palette drops. */
export function newStudioWriteConfig(): Record<StudioWriteKey, unknown> {
  return { package_id: "", action: "", data: {} };
}

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

/** One way to create a record: an object of the package, and the action that creates it. */
export interface CreateOffer {
  object: ObjectRow;
  /** The id of the declared action. It is the `config.action` of the step. */
  actionId: string;
}

/**
 * The create actions that the editor offers for a package.
 *
 * A viewer answer offers none, because the owner of the workflow must own the
 * package. The offer is a plain declared action (`verbMap` skips an action with
 * a `set`) whose `uses` is `object:<name>.create` for an object of the package.
 * An update, a delete and a read are never offered.
 */
export function createOffers(
  detail: Pick<PackageDetail, "access" | "actions" | "objects"> | null | undefined,
): CreateOffer[] {
  if (!detail || detail.access !== "owner") return [];
  const byUse = verbMap(detail.actions);
  const out: CreateOffer[] = [];
  for (const object of detail.objects ?? []) {
    if (!object || typeof object.name !== "string") continue;
    const actionId = byUse[`object:${object.name}.${CREATE_VERB}`];
    if (actionId) out.push({ object, actionId });
  }
  return out;
}

/** What the package step says, from one read of the package. */
export type PackageView =
  | { kind: "viewer"; text: string }
  | { kind: "none"; text: string }
  | { kind: "ready"; offers: CreateOffer[] };

export function packageView(detail: Pick<PackageDetail, "access" | "actions" | "objects">): PackageView {
  if (detail.access !== "owner") {
    return {
      kind: "viewer",
      text: "You can read this app, but you do not own it. Pick a package that you own.",
    };
  }
  const offers = createOffers(detail);
  if (offers.length === 0) {
    return {
      kind: "none",
      text: "This package declares no action that adds a record. Add one in Studio, then pick the package again.",
    };
  }
  return { kind: "ready", offers };
}

/** The fields of the data form of one object: the fields of its Form block. */
export function dataFields(object: Pick<ObjectRow, "fields" | "rules">): FieldSpec[] {
  return formFields(fieldSpecs(object), computedKeys(object.rules));
}

export function dataDrafts(fields: readonly FieldSpec[], data: unknown): Drafts {
  return initialDrafts(fields, isRecord(data) ? data : undefined);
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** Every string inside a value, as `_flatten_strings` finds them. */
function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (isRecord(value)) return Object.values(value).flatMap(strings);
  return [];
}

export type DataEdit =
  | { ok: true; patch: { data: Record<string, unknown> } }
  | { ok: false; errors: Record<string, string> };

/**
 * The patch for one submit of the data form, or the refusals by field.
 *
 * `checkForm().value` is exactly the `data` that a create takes. A value that
 * `validateRecord` refuses writes nothing. A value with `{{` writes nothing,
 * because publish refuses it, and the write that the owner approves must be the
 * write that runs.
 */
export function dataEdit(fields: readonly FieldSpec[], drafts: Drafts): DataEdit {
  const checked = checkForm(fields, drafts);
  if (!checked.ok) return { ok: false, errors: checked.errors };
  const errors: Record<string, string> = {};
  for (const [key, value] of Object.entries(checked.value)) {
    if (strings(value).some((s) => s.includes("{{"))) errors[key] = LITERAL_ONLY_MESSAGE;
  }
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, patch: { data: checked.value } };
}

/** The patch for a pick of a package: a new package clears the action and the values. */
export function packagePatch(packageId: string): { package_id: string; action: string; data: Record<string, unknown> } {
  return { package_id: packageId, action: "", data: {} };
}

/** The patch for a pick of an action: a new action clears the values. */
export function actionPatch(actionId: string): { action: string; data: Record<string, unknown> } {
  return { action: actionId, data: {} };
}

// -- a stored step that the form cannot edit -----------------------------------

const textOf = (config: Record<string, unknown>, key: string): string =>
  typeof config[key] === "string" ? (config[key] as string) : "";

/**
 * Why the form cannot edit a stored step, or `null` when it can.
 *
 * `detail` is `undefined` while the read is on its way, and `null` when the
 * package could not be read as an owner. `known` is whether the package is in
 * the list of the owner (`undefined` while the list loads).
 */
export function uneditableReason(
  config: Record<string, unknown>,
  known: boolean | undefined,
  detail: PackageView | null | undefined,
  fields: readonly FieldSpec[] | null,
): string | null {
  const extra = Object.keys(config).filter((k) => !(STUDIO_WRITE_KEYS as readonly string[]).includes(k));
  if (extra.length > 0) {
    return "This step updates or changes a record. The editor cannot change it yet.";
  }
  const pkg = textOf(config, "package_id");
  const action = textOf(config, "action");
  if (pkg === "") return null;
  if (known === false) return "This package is not one that you own, so the editor cannot change this step.";
  if (detail === null) return "The editor could not read this package, so it cannot change this step.";
  if (detail && detail.kind !== "ready") return detail.text;
  if (detail && action !== "" && !detail.offers.some((o) => o.actionId === action)) {
    return "This step uses an action that does not add a record, so the editor cannot change it.";
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

/** The summary of a stored step, in words. It holds no input. */
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
