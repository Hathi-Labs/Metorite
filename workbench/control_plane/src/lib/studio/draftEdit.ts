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
 * names of a deleted field in the views. A relation field of another object that links to a
 * deleted object blocks the delete, because that field is the member's data model to change.
 * What happens at publish is in `publishEffect`.
 */
import type { FieldType } from "./manifest";

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

function dropViews(m: Obj, ids: ReadonlySet<string>): Obj {
  if (ids.size === 0) return m;
  const cells = (cs: unknown) => (Array.isArray(cs) ? cs.filter((c) => !(isObj(c) && ids.has(str(c.view)))) : cs);
  const pages = Array.isArray(m.pages)
    ? m.pages.map((p) => {
        if (!isObj(p)) return p;
        const next: Obj = { ...p };
        if ("cells" in p) next.cells = cells(p.cells);
        if (Array.isArray(p.tabs)) next.tabs = p.tabs.map((t) => (isObj(t) ? { ...t, cells: cells(t.cells) } : t));
        if (isObj(p.side) && ids.has(str(p.side.view))) delete next.side;
        return next;
      })
    : m.pages;
  const views = Array.isArray(m.views) ? m.views.filter((v) => !(isObj(v) && ids.has(str(v.id)))) : m.views;
  return { ...m, views, ...(pages !== undefined ? { pages } : {}) };
}

/** Delete an object, its record actions, the views that show it, and the page cells of those views. */
export function removeObject(m: unknown, name: string): unknown {
  if (!isObj(m) || linksTo(m, name).length > 0) return m;
  const cut = objectCut(m, name);
  const withoutActions: Obj = {
    ...m,
    objects: objectsOf(m).filter((o) => str(o.name) !== name),
    ...(Array.isArray(m.actions) ? { actions: m.actions.filter((a) => !(isObj(a) && usesObject(a.uses, name))) } : {}),
  };
  const gone = new Set([...cut.actions]);
  const done = dropViews(withoutActions, new Set(cut.views));
  // A view that stays may list a deleted action in `actions`, or name it in `submit`.
  if (!Array.isArray(done.views) || gone.size === 0) return done;
  return {
    ...done,
    views: done.views.map((v) => {
      if (!isObj(v)) return v;
      const next: Obj = { ...v };
      if (Array.isArray(v.actions)) next.actions = v.actions.filter((a) => !gone.has(str(a)));
      if (gone.has(str(v.submit))) delete next.submit;
      return next;
    }),
  };
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
  if (!isObj(m)) return m;
  const removed = mapObject(m, object, (o) => ({
    ...o,
    fields: (Array.isArray(o.fields) ? o.fields : []).filter((f) => !(isObj(f) && str(f.key) === key)),
  })) as Obj;
  if (!Array.isArray(removed.views)) return removed;
  return {
    ...removed,
    views: removed.views.map((v) => {
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
  if (!live) return kind === "object" ? "It was never published, so the delete only changes this draft." : "It was never published, so the delete only changes this draft.";
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
