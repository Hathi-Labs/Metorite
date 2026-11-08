/**
 * The direct edits of a draft manifest in the Studio IDE (`ai_studio.md` §6.3, slice G3c).
 *
 * Every function here is pure. It takes a manifest, returns a new one, and never changes its
 * input. It never throws: a manifest of a bad shape comes back as it was. The Data tab and the
 * Views tab call these. They write the manifest only. One save (`draftSave.ts`) sends it.
 *
 * **What a delete does.** The draft is only text, so a delete first removes the item from the
 * draft. The parts that name it go too, so the draft stays valid for the server check: the record
 * actions of a deleted object, the views that show it, the page cells of a deleted view, and the
 * names of a deleted field in the views and in the `set` of the actions. The same holds for the
 * shell jobs, the skill actions, examples and cards, the workflow triggers, `shell.needs` and
 * `shell.search`. A relation field of another object that links to a deleted object blocks the
 * delete, because that field is the member's data model to change.
 * What happens at publish is in `publishEffect`.
 */
import type { FieldType } from "./manifest";

/** Every string inside a rule, for a scan. */
function ruleStrings(v: unknown, out: string[] = []): string[] {
  if (typeof v === "string") out.push(v);
  else if (Array.isArray(v)) v.forEach((x) => ruleStrings(x, out));
  else if (isObj(v)) for (const [k, x] of Object.entries(v)) if (k !== "message" && k !== "name") ruleStrings(x, out);
  return out;
}

const escapeRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * The rules that name a field, as `object: rule`. A rule names it as a key (`field`, `fields`, `target`,
 * `group_by`, a `where` atom) or inside a formula text. Rules of this object count, and so do the
 * roll-ups of other objects that read this object (`source`). A delete of such a field is blocked,
 * because the rule is the member's own logic to change.
 */
export function rulesNaming(m: unknown, object: string, key: string): string[] {
  const out: string[] = [];
  const word = new RegExp(`(^|[^A-Za-z0-9_])${escapeRe(key)}($|[^A-Za-z0-9_])`);
  for (const o of objectsOf(m)) {
    for (const r of list(o.rules)) {
      const own = str(o.name) === object;
      const reads = isObj(r) && str(r.source) === object;
      if (!own && !reads) continue;
      const hit = ruleStrings(r).some((t) => t === key || word.test(t));
      if (hit) out.push(`${str(o.name)}: ${str(r.name) || str(r.kind) || "rule"}`);
    }
  }
  return out;
}

/** The field types that may be hot (`studio_fields.HOT_TYPES`). */
const HOT_TYPES: readonly string[] = ["text", "select", "number", "money", "date", "datetime", "checkbox", "email", "relation"];

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** The field types that a member picks in the Data tab. Each needs nothing more than a label. */
export const SAFE_TYPES: readonly FieldType[] = [
  "text", "longText", "number", "date", "datetime", "checkbox", "select", "multiSelect", "url", "email", "phone",
];

export const TYPE_LABELS: Record<string, string> = {
  text: "Text", longText: "Long text", number: "Number", money: "Money", date: "Date", datetime: "Date and time",
  checkbox: "Yes or no", select: "One choice", multiSelect: "Many choices", person: "Person", relation: "Link to object",
  file: "File", url: "Web link", email: "Email", phone: "Phone", geo: "Place",
};

/** The blocks that the Views tab adds. */
export const VIEW_BLOCKS = ["table", "board"] as const;
export type ViewBlock = (typeof VIEW_BLOCKS)[number];

/** The longest label that the store keeps (`studio_fields.MAX_LABEL_CHARS`). */
export const MAX_LABEL = 120;

/** A name from a label: lower case, digits and `_`, starting with a letter, never one in `taken`. */
export function nameFrom(label: string, taken: ReadonlySet<string>, fallback: string, sep: "_" | "-" = "_"): string {
  let base = label.toLowerCase().replace(/[^a-z0-9]+/g, sep).replace(new RegExp(`^[^a-z]+`), "").replace(new RegExp(`${sep}+$`), "");
  if (!base) base = fallback;
  base = base.slice(0, 40).replace(new RegExp(`${sep}+$`), "") || fallback;
  let name = base;
  for (let n = 2; taken.has(name); n++) name = `${base}${sep}${n}`;
  return name;
}

const objectsOf = (m: unknown): Obj[] => (isObj(m) ? list(m.objects) : []);
const withObjects = (m: unknown, objects: unknown[]): unknown => (isObj(m) ? { ...m, objects } : m);

function mapObject(m: unknown, name: string, fn: (o: Obj) => Obj): unknown {
  if (!isObj(m) || !Array.isArray(m.objects)) return m;
  return withObjects(m, m.objects.map((o) => (isObj(o) && str(o.name) === name ? fn(o) : o)));
}

const clean = (label: string): string => label.replace(/\s+/g, " ").trim().slice(0, MAX_LABEL);

// ── Objects ─────────────────────────────────────────────────────────────────

/** Add an object. It starts with one `name` field, so the page has a column to draw. */
export function addObject(m: unknown, label: string): { manifest: unknown; name: string | null } {
  if (!isObj(m)) return { manifest: m, name: null };
  const taken = new Set(objectsOf(m).map((o) => str(o.name)));
  const shown = clean(label);
  const name = nameFrom(shown, taken, "item");
  const object: Obj = { name, ...(shown ? { label: shown } : {}), fields: [{ key: "name", type: "text", label: "Name" }] };
  return { manifest: withObjects(m, [...(Array.isArray(m.objects) ? m.objects : []), object]), name };
}

/** Set the label of an object. The name stays, because actions, views and records name it. */
export function renameObject(m: unknown, name: string, label: string): unknown {
  const shown = clean(label);
  return mapObject(m, name, (o) => {
    const { label: _old, ...rest } = o;
    void _old;
    return shown ? { ...rest, label: shown } : rest;
  });
}

const usesObject = (uses: unknown, name: string): boolean => str(uses).startsWith(`object:${name}.`);
const viewObject = (v: Obj): string => str(v.object) || (isObj(v.source) ? str(v.source.object) : "");

export interface ObjectCut {
  /** The ids of the actions that work on the object. */
  actions: string[];
  /** The ids of the views that show it. */
  views: string[];
}

/** What a delete of an object takes with it, for the confirm text. */
export function objectCut(m: unknown, name: string): ObjectCut {
  if (!isObj(m)) return { actions: [], views: [] };
  return {
    actions: list(m.actions).filter((a) => usesObject(a.uses, name)).map((a) => str(a.id)),
    views: list(m.views).filter((v) => viewObject(v) === name).map((v) => str(v.id)),
  };
}

/** The other objects whose relation fields link to `name`: `object.field` for each. */
export function linksTo(m: unknown, name: string): string[] {
  const out: string[] = [];
  for (const o of objectsOf(m)) {
    if (str(o.name) === name) continue;
    for (const f of list(o.fields)) if (str(f.type) === "relation" && str(f.to) === name) out.push(`${str(o.name)}.${str(f.key)}`);
  }
  return out;
}

/** The references to a set of views that live outside `views`: page cells, the side view, shell jobs, skill cards. */
function dropViews(m: Obj, ids: ReadonlySet<string>): Obj {
  if (ids.size === 0) return m;
  const cells = (cs: unknown) => (Array.isArray(cs) ? cs.filter((c) => !(isObj(c) && ids.has(str(c.view)))) : cs);
  const out: Obj = { ...m };
  if (Array.isArray(m.views)) out.views = m.views.filter((v) => !(isObj(v) && ids.has(str(v.id))));
  if (Array.isArray(m.pages)) {
    out.pages = m.pages.map((p) => {
      if (!isObj(p)) return p;
      const next: Obj = { ...p };
      if ("cells" in p) next.cells = cells(p.cells);
      if (Array.isArray(p.tabs)) next.tabs = p.tabs.map((t) => (isObj(t) ? { ...t, cells: cells(t.cells) } : t));
      if (isObj(p.side) && ids.has(str(p.side.view))) delete next.side;
      return next;
    });
    // A page that loses its last cell has no layout, and the check refuses it. It goes with its views.
    const held = (p: unknown) => isObj(p) && ((Array.isArray(p.tabs) && p.tabs.length > 0) || (Array.isArray(p.cells) && p.cells.length > 0));
    out.pages = (out.pages as unknown[]).filter((p, i) => held(p) || !held((m.pages as unknown[])[i]));
  }
  if (isObj(m.shell) && Array.isArray(m.shell.jobs)) {
    out.shell = { ...m.shell, jobs: m.shell.jobs.filter((j) => !(isObj(j) && ids.has(str(j.view)))) };
  }
  if (isObj(m.skill) && Array.isArray(m.skill.cards)) {
    out.skill = { ...(m.skill as Obj), cards: m.skill.cards.filter((c) => !ids.has(str(c))) };
  }
  return out;
}

/** The references to a set of actions: view `actions`, `submit` and `source.action`, and the skill. */
function dropActions(m: Obj, gone: ReadonlySet<string>, object?: string): Obj {
  // A skill names an action by its id, or by its `uses` (such as `object:visit.create`).
  const isGone = (a: unknown) => gone.has(str(a)) || (object !== undefined && usesObject(a, object));
  if (gone.size === 0 && object === undefined) return m;
  const out: Obj = { ...m };
  if (Array.isArray(m.views)) {
    out.views = m.views.map((v) => {
      if (!isObj(v)) return v;
      const next: Obj = { ...v };
      if (Array.isArray(v.actions)) next.actions = v.actions.filter((a) => !gone.has(str(a)));
      if (gone.has(str(v.submit))) delete next.submit;
      if (isObj(v.source) && gone.has(str(v.source.action))) {
        const { action: _a, ...rest } = v.source;
        void _a;
        if (Object.keys(rest).length > 0) next.source = rest;
        else delete next.source;
      }
      return next;
    });
  }
  if (isObj(m.skill)) {
    const skill: Obj = { ...(m.skill as Obj) };
    if (Array.isArray(skill.actions)) skill.actions = skill.actions.filter((a) => !isGone(a));
    if (Array.isArray(skill.examples)) {
      skill.examples = skill.examples.map((e) => {
        if (!isObj(e) || !isGone(e.action)) return e;
        const { action: _a, ...rest } = e;
        void _a;
        return rest;
      });
    }
    out.skill = skill;
  }
  return out;
}

/** Delete an object and everything in the manifest that names it, so the server check still passes. */
export function removeObject(m: unknown, name: string): unknown {
  if (!isObj(m) || linksTo(m, name).length > 0) return m;
  const cut = objectCut(m, name);
  let out: Obj = {
    ...m,
    objects: objectsOf(m).filter((o) => str(o.name) !== name),
    ...(Array.isArray(m.actions) ? { actions: m.actions.filter((a) => !(isObj(a) && usesObject(a.uses, name))) } : {}),
  };
  out = dropViews(out, new Set(cut.views));
  out = dropActions(out, new Set(cut.actions), name);
  if (Array.isArray(m.workflows)) {
    out.workflows = m.workflows.map((w) => {
      if (!isObj(w) || !isObj(w.trigger) || w.trigger.record !== name) return w;
      const { record: _r, ...trigger } = w.trigger;
      void _r;
      return { ...w, trigger };
    });
  }
  if (isObj(m.shell)) {
    const shell: Obj = { ...(out.shell as Obj) };
    if (Array.isArray(shell.needs)) shell.needs = shell.needs.filter((n) => !(isObj(n) && n.object === name));
    if (Array.isArray(shell.search)) shell.search = shell.search.filter((s) => s !== name);
    out.shell = shell;
  }
  return out;
}

// ── Fields ──────────────────────────────────────────────────────────────────

export interface FieldPatch {
  label?: string;
  type?: FieldType;
  required?: boolean;
  options?: string[];
}

const fieldsOf = (o: Obj): Obj[] => list(o.fields);

/** Add a field to an object. A select field starts with two options, so it validates. */
export function addField(m: unknown, object: string, label: string, type: FieldType = "text"): { manifest: unknown; key: string | null } {
  const o = objectsOf(m).find((x) => str(x.name) === object);
  if (!o) return { manifest: m, key: null };
  const shown = clean(label);
  const key = nameFrom(shown, new Set(fieldsOf(o).map((f) => str(f.key))), "field");
  const field: Obj = { key, type, ...(shown ? { label: shown } : {}) };
  if (type === "select" || type === "multiSelect") field.options = ["Option 1", "Option 2"];
  return {
    manifest: mapObject(m, object, (x) => ({ ...x, fields: [...(Array.isArray(x.fields) ? x.fields : []), field] })),
    key,
  };
}

/** Change the label, the type, the required flag or the options of one field. */
export function updateField(m: unknown, object: string, key: string, patch: FieldPatch): unknown {
  const out = changeField(m, object, key, patch);
  // A Board groups by a select field. A field that stops being one cannot be the lanes.
  if (patch.type === undefined || patch.type === "select" || !isObj(out) || !Array.isArray(out.views)) return out;
  return {
    ...out,
    views: out.views.map((v) => (isObj(v) && viewObject(v) === object && v.group_by === key ? withoutKey(v, "group_by") : v)),
  };
}

function withoutKey(v: Obj, key: string): Obj {
  const next: Obj = { ...v };
  delete next[key];
  return next;
}

function changeField(m: unknown, object: string, key: string, patch: FieldPatch): unknown {
  return mapObject(m, object, (o) => ({
    ...o,
    fields: (Array.isArray(o.fields) ? o.fields : []).map((f) => {
      if (!isObj(f) || str(f.key) !== key) return f;
      const next: Obj = { ...f };
      if (patch.label !== undefined) {
        const shown = clean(patch.label);
        if (shown) next.label = shown;
        else delete next.label;
      }
      if (patch.required !== undefined) {
        if (patch.required) next.required = true;
        else delete next.required;
      }
      if (patch.type !== undefined && patch.type !== f.type) {
        next.type = patch.type;
        // The store refuses `many` on a multiSelect or a checkbox, and `hot` on a type that cannot be hot.
        if (patch.type === "multiSelect" || patch.type === "checkbox") delete next.many;
        if (!HOT_TYPES.includes(patch.type) || next.many === true) delete next.hot;
        if (patch.type === "select" || patch.type === "multiSelect") {
          if (!Array.isArray(next.options) || next.options.length === 0) next.options = ["Option 1", "Option 2"];
        } else {
          delete next.options;
        }
      }
      if (patch.options !== undefined && (next.type === "select" || next.type === "multiSelect")) {
        next.options = patch.options.map((x) => x.trim()).filter(Boolean);
      }
      return next;
    }),
  }));
}

const FIELD_REFS = ["group_by", "start", "end", "sort"] as const;

/** Delete a field, and its name from the views of that object. */
export function removeField(m: unknown, object: string, key: string): unknown {
  if (!isObj(m) || rulesNaming(m, object, key).length > 0) return m;
  const removed = mapObject(m, object, (o) => ({
    ...o,
    fields: (Array.isArray(o.fields) ? o.fields : []).filter((f) => !(isObj(f) && str(f.key) === key)),
  })) as Obj;
  // An action of this object may name the field in its `set`. The check refuses a key that is no field.
  const cleaned: Obj = Array.isArray(removed.actions)
    ? {
        ...removed,
        actions: removed.actions.map((a) => {
          if (!isObj(a) || !usesObject(a.uses, object) || !isObj(a.set) || !(key in a.set)) return a;
          const set = { ...a.set };
          delete set[key];
          return Object.keys(set).length > 0 ? { ...a, set } : withoutKey(a, "set");
        }),
      }
    : removed;
  if (!Array.isArray(cleaned.views)) return cleaned;
  return {
    ...cleaned,
    views: cleaned.views.map((v) => {
      if (!isObj(v) || viewObject(v) !== object) return v;
      const next: Obj = { ...v };
      for (const a of ["fields", "columns"] as const) if (Array.isArray(v[a])) next[a] = v[a].filter((x) => x !== key);
      for (const r of FIELD_REFS) if (v[r] === key) delete next[r];
      if (isObj(v.card)) {
        const card: Obj = { ...v.card };
        if (card.title === key) delete card.title;
        if (Array.isArray(card.meta)) card.meta = card.meta.filter((x) => x !== key);
        next.card = card;
      }
      return next;
    }),
  };
}

// ── Views ───────────────────────────────────────────────────────────────────

/** The first select field of one value with options in an object, for the lanes of a Board. */
export function laneField(o: Obj | undefined): string | null {
  const f = o ? fieldsOf(o).find((x) => str(x.type) === "select" && x.many !== true && Array.isArray(x.options) && x.options.length > 0) : undefined;
  return f ? str(f.key) : null;
}

/** The objects a block can show: a Board needs a select field to group by. */
export function objectsFor(m: unknown, block: string): string[] {
  return objectsOf(m).filter((o) => str(o.name) && (block !== "board" || laneField(o))).map((o) => str(o.name));
}

function viewShape(m: unknown, block: string, object: string, base: Obj): Obj {
  const o = objectsOf(m).find((x) => str(x.name) === object);
  const next: Obj = { ...base, block, source: { object } };
  delete next.object;
  for (const k of ["group_by", "card", "fields", "columns", "sort", "start", "end"]) delete next[k];
  if (block === "board") {
    const lane = laneField(o);
    const first = o ? fieldsOf(o).find((f) => str(f.type) === "text" || str(f.type) === "longText") : undefined;
    if (lane) next.group_by = lane;
    if (first) next.card = { title: str(first.key) };
  }
  return next;
}

/** Add a view of a block over an object. A Board needs an object with a select field. */
export function addView(m: unknown, block: ViewBlock, object: string, title: string): { manifest: unknown; id: string | null } {
  if (!isObj(m) || !objectsFor(m, block).includes(object)) return { manifest: m, id: null };
  const shown = clean(title);
  const id = nameFrom(shown || `${object}-${block}`, new Set(list(m.views).map((v) => str(v.id))), block, "-");
  const view = viewShape(m, block, object, { id, ...(shown ? { title: shown } : {}) });
  return { manifest: { ...m, views: [...(Array.isArray(m.views) ? m.views : []), view] }, id };
}

/** Change the title or the object of a view. A new object resets the fields that name the old one. */
export function updateView(m: unknown, id: string, patch: { title?: string; object?: string }): unknown {
  if (!isObj(m) || !Array.isArray(m.views)) return m;
  return {
    ...m,
    views: m.views.map((v) => {
      if (!isObj(v) || str(v.id) !== id) return v;
      let next: Obj = { ...v };
      if (patch.title !== undefined) {
        const shown = clean(patch.title);
        if (shown) next.title = shown;
        else delete next.title;
      }
      if (patch.object !== undefined && patch.object !== viewObject(v) && objectsFor(m, str(v.block)).includes(patch.object)) {
        next = viewShape(m, str(v.block), patch.object, next);
      }
      return next;
    }),
  };
}

/** Delete a view, and the page cells that show it. */
export function removeView(m: unknown, id: string): unknown {
  return isObj(m) ? dropViews(m, new Set([id])) : m;
}

/** The page cells that show a view, for the confirm text. */
export function viewUses(m: unknown, id: string): number {
  if (!isObj(m) || !Array.isArray(m.pages)) return 0;
  let n = 0;
  const count = (cs: unknown) => { if (Array.isArray(cs)) n += cs.filter((c) => isObj(c) && str(c.view) === id).length; };
  for (const p of m.pages) {
    if (!isObj(p)) continue;
    count(p.cells);
    if (Array.isArray(p.tabs)) p.tabs.forEach((t) => isObj(t) && count(t.cells));
    if (isObj(p.side) && str(p.side.view) === id) n++;
  }
  return n;
}

// ── What a publish does with a change ───────────────────────────────────────

/** The object rows of the gateway that a publish made: what is live. */
export interface LiveObject {
  name: string;
  fields: Array<{ key: string; type?: string; label?: string | null; required?: boolean; options?: string[] | null }>;
}

/**
 * The words for a delete of a LIVE object. The sync of a publish (`objects_store._plan_sync`) takes
 * a published object or field out of no row. It refuses the publish: 409 `objects_out_of_sync`,
 * "A published object cannot go yet". The live version and its records stay. So the delete holds
 * in the draft, and a publish stays blocked until the object is back or the app is made again.
 */
export function publishEffect(kind: "object" | "field", live: boolean): string {
  if (!live) return "It was never published, so the delete only changes this draft.";
  return kind === "object"
    ? "This object is live. The delete changes this draft only. Publish is refused while the object is missing (A published object cannot go yet), and the live app and its records stay as they are."
    : "This field is live. The delete changes this draft only. Publish is refused while the field is missing (A published field cannot go yet), and the live app and its records stay as they are.";
}

/** The note for a change to a live field: a published field cannot change yet. */
export const LIVE_FIELD_NOTE =
  "This field is live. Publish is refused while its type, required flag, options or label differ from the live field. Revert to publish.";

/** True when the field is in the live object rows. */
export function isLiveField(live: readonly LiveObject[], object: string, key: string): boolean {
  return !!live.find((o) => o.name === object)?.fields.some((f) => f.key === key);
}

export const isLiveObject = (live: readonly LiveObject[], name: string): boolean => live.some((o) => o.name === name);

/**
 * What a text control commits on blur or Enter: the trimmed text, or null when it equals the shown
 * value. The control keeps the raw text while the member types, so a space between two words
 * is never eaten by a trim on each key.
 */
export function commitText(shown: string, typed: string): string | null {
  const next = typed.replace(/\s+/g, " ").trim();
  return next === shown.trim() ? null : next;
}
