/**
 * The Problems panel of the Studio IDE (`ai_studio.md` §6.3, slice G3a).
 *
 * No gateway route answers "what is wrong with this draft". The checker
 * (`validate_manifest`) runs inside the tools of Mira and at a save. So the panel runs a small
 * set of STRUCTURAL checks in the browser, on the draft that the page already holds. It is
 * a help to read, not the gate: the server check at a save and a publish stays the gate,
 * and a draft that passes here can still be refused there. The panel says so.
 * A bad or missing manifest gives one error and never a throw.
 */
import { FIELD_TYPE_LIST } from "./blocks";

export type Severity = "error" | "warning";

export interface Problem {
  severity: Severity;
  /** A JSON path such as `$.objects[0].fields[1]`. */
  path: string;
  /** One sentence: what is wrong and what to do. */
  message: string;
}

export const PANEL_NOTE = "These are quick checks in your browser. The full check runs when Mira saves and when you publish.";
const ID_RE = /^[a-z][a-z0-9_-]{0,47}$/;
const BLOCKS = new Set([
  "table", "form", "detail", "board", "chart", "stat", "calendar", "timeline", "mindMap", "processFlow",
  "checklist", "map", "markdown",
]);
/** What a chat can hold. A draft over it must change in Studio. */
export const CHAT_CAP = 24_000;

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isObject) : []);
const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

export function checkDraft(manifest: unknown): Problem[] {
  if (!isObject(manifest)) {
    return [{ severity: "error", path: "$", message: "The draft is not a manifest. Ask Mira to rebuild it." }];
  }
  const out: Problem[] = [];
  const err = (path: string, message: string) => out.push({ severity: "error", path, message });
  const warn = (path: string, message: string) => out.push({ severity: "warning", path, message });

  if (manifest.studio !== 1) err("$.studio", "The `studio` value must be the whole number 1.");
  if (!ID_RE.test(text(manifest.id))) err("$.id", "The app id is lowercase letters, digits, - and _, and starts with a letter.");
  if (manifest.kind !== "system") {
    if (!text(manifest.name)) err("$.name", "The app needs a name. Ask Mira to name it.");
    if (!text(manifest.purpose)) err("$.purpose", "The app needs a purpose line. Ask Mira to write one.");
  }

  const objects = list(manifest.objects);
  const names = new Set<string>();
  objects.forEach((o, i) => {
    const at = `$.objects[${i}]`;
    const name = text(o.name);
    if (!name) err(`${at}.name`, "An object needs a name.");
    else if (names.has(name)) err(`${at}.name`, `Two objects are named ${name}. Rename one.`);
    names.add(name);
    const fields = list(o.fields);
    if (fields.length === 0) warn(`${at}.fields`, `The ${name || "object"} object has no field yet.`);
    const keys = new Set<string>();
    fields.forEach((f, j) => {
      const fat = `${at}.fields[${j}]`;
      const key = text(f.key);
      if (!key) err(`${fat}.key`, "A field needs a key.");
      else if (keys.has(key)) err(`${fat}.key`, `Two fields are named ${key} in ${name || "this object"}. Rename one.`);
      keys.add(key);
      const type = text(f.type);
      if (!FIELD_TYPE_LIST.includes(type as never)) err(`${fat}.type`, `The field type ${type || "(none)"} is not one Studio knows.`);
      if ((type === "select" || type === "multiSelect") && !(Array.isArray(f.options) && f.options.length > 0)) {
        warn(`${fat}.options`, `The ${key || "select"} field has no options yet.`);
      }
      if (type === "relation" && !text(f.to)) err(`${fat}.to`, `The ${key || "relation"} field names no object to link to.`);
    });
  });
  // A relation may point at an object later in the list, so check after all names are known.
  objects.forEach((o, i) => list(o.fields).forEach((f, j) => {
    const to = text(f.to);
    if (text(f.type) === "relation" && to && !names.has(to)) {
      err(`$.objects[${i}].fields[${j}].to`, `The field links to ${to}, and the app has no such object.`);
    }
  }));

  const actions = list(manifest.actions);
  const actionIds = new Set<string>();
  actions.forEach((a, i) => {
    const at = `$.actions[${i}]`;
    const id = text(a.id);
    if (!id) err(`${at}.id`, "An action needs an id.");
    else if (actionIds.has(id)) err(`${at}.id`, `Two actions are named ${id}. Rename one.`);
    actionIds.add(id);
    const uses = text(a.uses);
    if (!uses) err(`${at}.uses`, `The ${id || "action"} action names nothing to run.`);
    const m = /^object:([a-z][a-z0-9_]*)\./.exec(uses);
    if (m && !names.has(m[1])) err(`${at}.uses`, `The ${id || "action"} action works on ${m[1]}, and the app has no such object.`);
  });

  const viewIds = new Set<string>();
  list(manifest.views).forEach((v, i) => {
    const at = `$.views[${i}]`;
    const id = text(v.id);
    if (!id) err(`${at}.id`, "A view needs an id.");
    else if (viewIds.has(id)) err(`${at}.id`, `Two views are named ${id}. Rename one.`);
    viewIds.add(id);
    const block = text(v.block);
    if (!BLOCKS.has(block)) err(`${at}.block`, `The view block ${block || "(none)"} is not one Studio knows.`);
    const object = text(v.object);
    if (object && !names.has(object)) err(`${at}.object`, `The ${id || "view"} view shows ${object}, and the app has no such object.`);
    if (!object && !isObject(v.source) && block !== "markdown") {
      warn(`${at}.object`, `The ${id || "view"} view has no object or source, so it shows nothing yet.`);
    }
  });

  if (objects.length === 0) warn("$.objects", "The app holds no object yet. Ask Mira what it should track.");
  for (const o of objects) {
    const name = text(o.name);
    if (name && !actions.some((a) => text(a.uses) === `object:${name}.list`)) {
      warn("$.actions", `The ${name} object has no list action, so its records cannot show.`);
    }
  }

  const size = JSON.stringify(manifest).length;
  if (size > CHAT_CAP) warn("$", `The draft is ${size} characters. Mira can hold ${CHAT_CAP}. Split the app into smaller parts.`);
  return out;
}

export function countProblems(problems: readonly Problem[]): { errors: number; warnings: number } {
  const errors = problems.filter((p) => p.severity === "error").length;
  return { errors, warnings: problems.length - errors };
}

/** The panel header: "0 errors · 1 warning". */
export function problemsLine(problems: readonly Problem[]): string {
  const { errors, warnings } = countProblems(problems);
  return `${errors} error${errors === 1 ? "" : "s"} · ${warnings} warning${warnings === 1 ? "" : "s"}`;
}
