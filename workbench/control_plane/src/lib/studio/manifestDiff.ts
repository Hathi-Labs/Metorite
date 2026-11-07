/**
 * What a change does to an app, in plain words (`ai_studio.md` §14, slice G5). Pure.
 *
 * `diffManifests(before, after)` finds the changes between two manifests. Records are keyed by
 * `name`, fields by `key` inside their record, and views, actions, pages and workflows by `id`. So
 * a reorder is no change. `diffLines` turns the changes into short lines, with each name inside
 * « ». The IDE shows them in the change bar (after a change lands in the draft) and in the Publish
 * dialog (before a change goes live).
 *
 * A name comes from the manifest, and nobody can trust it. So a name loses its invisible and control
 * characters, loses « and », and stops at 36 characters (the rule of the chat card names).
 */
import { stableJson } from "./dataDoor";

export type ChangeKind =
  | "app-name" | "app-purpose"
  | "object-add" | "object-remove" | "object-label" | "object-rules"
  | "field-add" | "field-remove" | "field-label" | "field-type" | "field-required" | "field-optional"
  | "field-options" | "field-other"
  | "view-add" | "view-remove" | "view-title" | "view-object" | "view-block" | "view-other"
  | "action-add" | "action-remove" | "action-other" | "record-actions-add" | "record-actions-remove"
  | "page-add" | "page-remove" | "page-other"
  | "workflow-add" | "workflow-remove" | "workflow-other"
  | "part";

export interface Change {
  kind: ChangeKind;
  /** The thing that changed: a record, a field, a view, an action, a page, a workflow or a part. */
  name: string;
  /** The record that holds a field. */
  in?: string;
  /** The value before and after, for a rename or a retype. */
  from?: string;
  to?: string;
  /** The record actions that one line folds: list, get, create, update, delete. */
  verbs?: string[];
}

export const MAX_NAME = 36;
const HIDDEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}­͏ᅟᅠㅤﾠ⠀]/gu;

/** A name for a line: no hidden characters, no « or », one line, at most 36 characters. */
export function cleanName(raw: unknown): string {
  const text = typeof raw === "string" ? raw : typeof raw === "number" ? String(raw) : "";
  const flat = text.replace(/\s+/g, " ").replace(HIDDEN, "").replace(/[«»]/g, "").trim();
  const chars = [...flat];
  return chars.length > MAX_NAME ? `${chars.slice(0, MAX_NAME - 1).join("")}…` : flat;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const same = (a: unknown, b: unknown): boolean => stableJson(a ?? null) === stableJson(b ?? null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isObject) : []);

/** Index the items of a list by a key. The first item of a key wins. */
function byKey(items: Record<string, unknown>[], key: string): Map<string, Record<string, unknown>> {
  const out = new Map<string, Record<string, unknown>>();
  for (const it of items) {
    const k = str(it[key]);
    if (k && !out.has(k)) out.set(k, it);
  }
  return out;
}

/** What shows for a record or a field: its label, else its key. */
const shown = (item: Record<string, unknown>, key: string): string => cleanName(str(item.label) || str(item[key]));
const viewShown = (v: Record<string, unknown>): string => cleanName(str(v.title) || str(v.id));
const actionShown = (a: Record<string, unknown>): string => cleanName(str(a.title) || str(a.id));

const HANDLED = new Set(["studio", "kind", "id", "name", "purpose", "objects", "actions", "views", "pages", "workflows"]);
const PART_WORDS: Record<string, string> = {
  connections: "connections", team: "team", skill: "skill of the app", shell: "shell settings",
  contributes: "contributions", extends: "base app", slots: "slots", needs: "needs", overrides: "overrides",
};

/** Compare two lists of items with a key. `add`, `remove` and `both` make the changes. */
function compareKeyed(
  before: Record<string, unknown>[], after: Record<string, unknown>[], key: string,
  on: { add: (a: Record<string, unknown>) => void; remove: (b: Record<string, unknown>) => void; both: (b: Record<string, unknown>, a: Record<string, unknown>) => void },
): void {
  const b = byKey(before, key);
  const a = byKey(after, key);
  for (const [k, item] of a) if (!b.has(k)) on.add(item);
  for (const [k, item] of b) if (!a.has(k)) on.remove(item);
  for (const [k, item] of a) {
    const old = b.get(k);
    if (old && !same(old, item)) on.both(old, item);
  }
}

function diffFields(out: Change[], obj: string, before: unknown, after: unknown): void {
  compareKeyed(list(before), list(after), "key", {
    add: (f) => out.push({ kind: "field-add", name: shown(f, "key"), in: obj }),
    remove: (f) => out.push({ kind: "field-remove", name: shown(f, "key"), in: obj }),
    both: (b, a) => {
      const name = shown(a, "key");
      if (str(b.label) !== str(a.label)) out.push({ kind: "field-label", name, in: obj, from: cleanName(str(b.label) || str(b.key)), to: name });
      if (b.type !== a.type) out.push({ kind: "field-type", name, in: obj, from: cleanName(b.type), to: cleanName(a.type) });
      if (!!b.required !== !!a.required) out.push({ kind: a.required ? "field-required" : "field-optional", name, in: obj });
      if (!same(b.options, a.options)) out.push({ kind: "field-options", name, in: obj });
      // Another property changed (relation target, many, hot). Say so once.
      const rest = (f: Record<string, unknown>) => ({ ...f, label: 0, type: 0, required: 0, options: 0 });
      if (!same(rest(b), rest(a))) out.push({ kind: "field-other", name, in: obj });
    },
  });
}

function diffObjects(out: Change[], before: unknown, after: unknown): void {
  compareKeyed(list(before), list(after), "name", {
    add: (o) => out.push({ kind: "object-add", name: shown(o, "name") }),
    remove: (o) => out.push({ kind: "object-remove", name: shown(o, "name") }),
    both: (b, a) => {
      const name = shown(a, "name");
      if (str(b.label) !== str(a.label)) out.push({ kind: "object-label", name: cleanName(str(b.label) || str(b.name)), from: cleanName(str(b.label) || str(b.name)), to: cleanName(str(a.label) || str(a.name)) });
      diffFields(out, name, b.fields, a.fields);
      if (!same(b.rules, a.rules)) out.push({ kind: "object-rules", name });
    },
  });
}

function diffViews(out: Change[], before: unknown, after: unknown): void {
  compareKeyed(list(before), list(after), "id", {
    add: (v) => out.push({ kind: "view-add", name: viewShown(v) }),
    remove: (v) => out.push({ kind: "view-remove", name: viewShown(v) }),
    both: (b, a) => {
      const name = viewShown(a);
      if (str(b.title) !== str(a.title)) out.push({ kind: "view-title", name: viewShown(b), from: viewShown(b), to: viewShown(a) });
      if (str(b.object) !== str(a.object)) out.push({ kind: "view-object", name, from: cleanName(b.object), to: cleanName(a.object) });
      if (b.block !== a.block) out.push({ kind: "view-block", name, from: cleanName(b.block), to: cleanName(a.block) });
      const rest = (v: Record<string, unknown>) => ({ ...v, title: 0, object: 0, block: 0 });
      if (!same(rest(b), rest(a))) out.push({ kind: "view-other", name });
    },
  });
}

/**
 * The record actions of a record type: the ones that `complete_record_actions` adds
 * (`packages/acb_skills/acb_skills/studio_tools.py`, `OBJECT_OPS`). Each has `uses` of the form
 * `object:<record>.<verb>`. The match reads `uses`, never the id, so a renamed id still folds.
 */
export const RECORD_VERBS = ["list", "get", "create", "update", "delete"] as const;
const VERB_WORDS: Record<string, string> = { list: "list", get: "open", create: "create", update: "change", delete: "delete" };
const RECORD_USES = /^object:([^.]+)\.(list|get|create|update|delete)$/;
const recordUse = (a: Record<string, unknown>): { object: string; verb: string } | null => {
  const m = RECORD_USES.exec(str(a.uses));
  return m ? { object: m[1], verb: m[2] } : null;
};

/** One change for each record type whose record actions came or went, in the order of the first action. */
function foldRecordActions(items: Record<string, unknown>[], objects: unknown, kind: "record-actions-add" | "record-actions-remove"): Change[] {
  const labels = new Map(list(objects).map((o) => [str(o.name), shown(o, "name")]));
  const byObject = new Map<string, Set<string>>();
  for (const a of items) {
    const u = recordUse(a);
    if (!u) continue;
    if (!byObject.has(u.object)) byObject.set(u.object, new Set());
    byObject.get(u.object)!.add(u.verb);
  }
  return [...byObject].map(([obj, have]) => ({
    kind, name: labels.get(obj) ?? cleanName(obj), verbs: RECORD_VERBS.filter((v) => have.has(v)),
  }));
}

function diffActions(out: Change[], before: unknown, after: unknown, beforeObjects: unknown, afterObjects: unknown): void {
  const b = byKey(list(before), "id");
  const a = byKey(list(after), "id");
  const added = [...a].filter(([k]) => !b.has(k)).map(([, v]) => v);
  const removed = [...b].filter(([k]) => !a.has(k)).map(([, v]) => v);
  out.push(...foldRecordActions(added, afterObjects, "record-actions-add"));
  out.push(...foldRecordActions(removed, beforeObjects, "record-actions-remove"));
  for (const x of added) if (!recordUse(x)) out.push({ kind: "action-add", name: actionShown(x) });
  for (const x of removed) if (!recordUse(x)) out.push({ kind: "action-remove", name: actionShown(x) });
  for (const [k, item] of a) {
    const old = b.get(k);
    if (old && !same(old, item)) out.push({ kind: "action-other", name: actionShown(item) });
  }
}

function diffSimple(out: Change[], before: unknown, after: unknown, key: "page" | "workflow"): void {
  const shownBy = (x: Record<string, unknown>) => (key === "workflow" ? cleanName(str(x.id)) : actionShown(x));
  compareKeyed(list(before), list(after), "id", {
    add: (x) => out.push({ kind: `${key}-add` as ChangeKind, name: shownBy(x) }),
    remove: (x) => out.push({ kind: `${key}-remove` as ChangeKind, name: shownBy(x) }),
    both: (_b, a) => out.push({ kind: `${key}-other` as ChangeKind, name: shownBy(a) }),
  });
}

/** The changes from `before` to `after`. `before` null is a first version: every record, view and action is an add. */
export function diffManifests(before: unknown, after: unknown): Change[] {
  const out: Change[] = [];
  const a = isObject(after) ? after : {};
  const b = isObject(before) ? before : null;
  if (!b) {
    for (const o of list(a.objects)) out.push({ kind: "object-add", name: shown(o, "name") });
    for (const v of list(a.views)) out.push({ kind: "view-add", name: viewShown(v) });
    diffActions(out, [], a.actions, null, a.objects);
    return out;
  }
  if (str(b.name) !== str(a.name)) out.push({ kind: "app-name", name: cleanName(b.name), from: cleanName(b.name), to: cleanName(a.name) });
  if (str(b.purpose) !== str(a.purpose)) out.push({ kind: "app-purpose", name: "purpose" });
  diffObjects(out, b.objects, a.objects);
  diffViews(out, b.views, a.views);
  diffActions(out, b.actions, a.actions, b.objects, a.objects);
  diffSimple(out, b.pages, a.pages, "page");
  diffSimple(out, b.workflows, a.workflows, "workflow");
  for (const key of [...new Set([...Object.keys(b), ...Object.keys(a)])].sort()) {
    if (HANDLED.has(key)) continue;
    if (!same(b[key], a[key])) out.push({ kind: "part", name: PART_WORDS[key] ?? cleanName(key) });
  }
  return out;
}

const q = (s: string) => `«${s}»`;

/** One change as a plain line, with no capital and no full stop. */
export function changeLine(c: Change, first = false): string {
  const n = q(c.name);
  const inn = c.in ? ` in ${q(c.in)}` : "";
  switch (c.kind) {
    case "app-name": return `renames the app from ${q(c.from ?? "")} to ${q(c.to ?? "")}`;
    case "app-purpose": return "changes the purpose of the app";
    case "object-add": return first ? `makes the record type ${n}` : `adds the record type ${n}`;
    case "object-remove": return `removes the record type ${n}`;
    case "object-label": return `renames the record type ${q(c.from ?? "")} to ${q(c.to ?? "")}`;
    case "object-rules": return `changes the rules of ${n}`;
    case "field-add": return `adds the field ${n} to ${q(c.in ?? "")}`;
    case "field-remove": return `removes the field ${n} from ${q(c.in ?? "")}`;
    case "field-label": return `renames the field ${q(c.from ?? "")} to ${q(c.to ?? "")}${inn}`;
    case "field-type": return `changes the type of ${n}${inn} from ${q(c.from ?? "")} to ${q(c.to ?? "")}`;
    case "field-required": return `makes the field ${n}${inn} required`;
    case "field-optional": return `makes the field ${n}${inn} optional`;
    case "field-options": return `changes the options of ${n}${inn}`;
    case "field-other": return `changes the field ${n}${inn}`;
    case "view-add": return first ? `makes the view ${n}` : `adds the view ${n}`;
    case "view-remove": return `removes the view ${n}`;
    case "view-title": return `renames the view ${q(c.from ?? "")} to ${q(c.to ?? "")}`;
    case "view-object": return `moves the view ${n} to the record type ${q(c.to ?? "")}`;
    case "view-block": return `changes the view ${n} from ${q(c.from ?? "")} to ${q(c.to ?? "")}`;
    case "view-other": return `changes the view ${n}`;
    case "action-add": return first ? `makes the action ${n}` : `adds the action ${n}`;
    case "action-remove": return `removes the action ${n}`;
    case "action-other": return `changes the action ${n}`;
    case "page-add": return `adds the page ${n}`;
    case "page-remove": return `removes the page ${n}`;
    case "page-other": return `changes the page ${n}`;
    case "workflow-add": return `adds the workflow ${n}`;
    case "workflow-remove": return `removes the workflow ${n}`;
    case "workflow-other": return `changes the workflow ${n}`;
    case "record-actions-add": return `${first ? "makes" : "adds"} the record actions of ${n} (${(c.verbs ?? []).map((v) => VERB_WORDS[v] ?? v).join(", ")})`;
    case "record-actions-remove": return `removes the record actions of ${n} (${(c.verbs ?? []).map((v) => VERB_WORDS[v] ?? v).join(", ")})`;
    case "part": return `changes the ${c.name}`;
  }
}

/** At most `limit` lines, then "and N more changes". `first` words a first version ("makes ..."). */
export function diffLines(changes: Change[], limit = 12, first = false): string[] {
  const lines = changes.slice(0, limit).map((c) => changeLine(c, first));
  const more = changes.length - lines.length;
  if (more > 0) lines.push(`and ${more} more change${more === 1 ? "" : "s"}`);
  return lines;
}
