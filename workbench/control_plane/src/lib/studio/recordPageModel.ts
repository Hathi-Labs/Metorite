/**
 * The model of the Studio record page (STU-AF5, `ai_studio.md` §5.5 and the AF-5 row).
 * Pure: no React, no I/O.
 *
 * A record opens in the side panel, or as a full page with its own URL. Its tabs are Details (the
 * Detail block), Related (a table for each relation field of another object that points to this
 * object), Lines (the sheet of each `lines` field, STU-D17a) and Files (the `file` fields). The
 * Activity tab joins when D22 is built.
 *
 * **The tabs.** An object may declare `record: { tabs: [...] }`. The tab ids it names, in its
 * order, win. The manifest schema has no `record` key yet, so no app declares one today. Without
 * it, §5.5 rule 3 gives the default: Details always, Lines when the object has a `lines` field,
 * Related when a relation points here, Files when the object has a file field. A declared Lines
 * tab on an object with no `lines` field is dropped, because it would be empty.
 *
 * **The URL.** `rec` is the record id and `ro` is its object. `rtab` is the record tab, and `full=1`
 * is the full page. `rtab` is not `tab`: `tab` belongs to the tabs of the page. `pageState.ts` bounds
 * the four keys, so a hostile link gives a closed panel and never an error.
 *
 * **The related table.** It reads `object:<child>.list` through the door with the filter
 * `<relation field> eq <record id>`. A relation field filters with `eq` (`studio_query.OPS_BY_TYPE`).
 * The door and the store hold the row rules, so the table shows what the viewer reads.
 */
import type { StudioDataDoor } from "./dataDoor";
import type { PageState } from "./pageState";

export type RecordTabId = "details" | "lines" | "related" | "files";

export const RECORD_TAB_IDS: readonly RecordTabId[] = ["details", "lines", "related", "files"];

export const RECORD_TAB_TITLES: Readonly<Record<RecordTabId, string>> = {
  details: "Details",
  lines: "Lines",
  related: "Related",
  files: "Files",
};

/** The most children that a related table reads. The store gives at most 100. */
export const RELATED_LIMIT = 100;

/** One field of an object, as the gateway gives it. */
export interface RecordField {
  key: string;
  type: string;
  label?: string | null;
  many?: boolean;
  to?: string | null;
  hot?: boolean;
  lines?: string | null;
}

export interface RecordObject {
  name: string;
  label?: string | null;
  fields?: readonly RecordField[];
  /** The tabs of the record page. The manifest schema has no such key yet (see the file header). */
  record?: { tabs?: readonly unknown[] } | null;
}

export interface RelatedList {
  /** The child object, which holds the relation field. */
  object: string;
  objectLabel: string;
  /** The relation field of the child. */
  field: string;
  fieldLabel: string;
  title: string;
}

const text = (v: unknown, fallback: string): string => (typeof v === "string" && v.trim() ? v.trim() : fallback);

/**
 * A related table for each relation field of another object that points to `name`. A target of
 * another app (`app.object`) is not a bare name, so it never matches. A lines relation (C13) is the
 * parent side of a line grid and holds no value, so it is no link.
 */
export function relatedLists(name: string, objects: readonly RecordObject[]): RelatedList[] {
  const out: RelatedList[] = [];
  // The back-link of a lines child shows in the Lines tab. Related would show the same rows twice.
  const backLinks = new Set(linesFields(name, objects).map((l) => `${l.child}.${l.back}`));
  for (const o of objects) {
    if (o.name === name) continue;
    for (const f of o.fields ?? []) {
      if (f.type !== "relation" || f.many === true || typeof f.lines === "string" || f.to !== name) continue;
      if (backLinks.has(`${o.name}.${f.key}`)) continue;
      const objectLabel = text(o.label, o.name);
      const fieldLabel = text(f.label, f.key);
      out.push({ object: o.name, objectLabel, field: f.key, fieldLabel, title: `${objectLabel} by ${fieldLabel}` });
    }
  }
  return out;
}

/** One lines field of a parent object (C13): its sheet is one child object, joined by the back-link. */
export interface LinesField {
  /** The lines field of the parent. */
  key: string;
  label: string;
  /** The line object. */
  child: string;
  /** The relation field of the line object that points back to the parent. */
  back: string;
}

/** The lines fields of the object `name`, in field order. A target that names no object of the app is left out. */
export function linesFields(name: string, objects: readonly RecordObject[]): LinesField[] {
  const parent = objects.find((o) => o.name === name);
  const out: LinesField[] = [];
  for (const f of parent?.fields ?? []) {
    if (f.type !== "relation" || typeof f.lines !== "string" || typeof f.to !== "string") continue;
    if (!objects.some((o) => o.name === f.to)) continue;
    out.push({ key: f.key, label: text(f.label, f.key), child: f.to, back: f.lines });
  }
  return out;
}

/** The file fields of an object. */
export function fileFields(object: RecordObject): RecordField[] {
  return (object.fields ?? []).filter((f) => f.type === "file");
}

export interface RecordTab {
  id: RecordTabId;
  title: string;
}

function declared(object: RecordObject): RecordTabId[] | null {
  const raw = object.record?.tabs;
  if (!Array.isArray(raw)) return null;
  const out: RecordTabId[] = [];
  for (const t of raw) {
    if (typeof t === "string" && (RECORD_TAB_IDS as readonly string[]).includes(t) && !out.includes(t as RecordTabId)) {
      out.push(t as RecordTabId);
    }
  }
  return out.length ? out : null;
}

/** The tabs of the record page of `object`. Never empty: Details is always there. */
export function recordTabs(object: RecordObject, objects: readonly RecordObject[]): RecordTab[] {
  const ids = declared(object) ?? [
    "details" as const,
    ...(linesFields(object.name, objects).length ? ["lines" as const] : []),
    ...(relatedLists(object.name, objects).length ? ["related" as const] : []),
    ...(fileFields(object).length ? ["files" as const] : []),
  ];
  const hasLines = linesFields(object.name, objects).length > 0;
  return ids.filter((id) => id !== "lines" || hasLines).map((id) => ({ id, title: RECORD_TAB_TITLES[id] }));
}

/** The tab to show: the wanted one when the object has it, else the first. */
export function pickRecordTab(tabs: readonly RecordTab[], wanted: string | null | undefined): RecordTabId {
  return tabs.find((t) => t.id === wanted)?.id ?? tabs[0]?.id ?? "details";
}

/** The `list` input of a related table: the children of one record. */
export function relatedArgs(list: Pick<RelatedList, "field">, recordId: string): {
  limit: number; filter: { field: string; op: "eq"; value: string }[];
} {
  return { limit: RELATED_LIMIT, filter: [{ field: list.field, op: "eq", value: recordId }] };
}

// --- the URL ----------------------------------------------------------------

/** Open a record. The tab and the full page start over. */
export function openRecord(s: PageState, object: string, id: string): PageState {
  return { ...s, ro: object, rec: id, rtab: undefined, full: undefined };
}

/** Close the record, whether it shows in the panel or as the full page. */
export function closeRecord(s: PageState): PageState {
  return { ...s, ro: undefined, rec: undefined, rtab: undefined, full: undefined };
}

export function withRecordTab(s: PageState, tab: string): PageState {
  return { ...s, rtab: tab };
}

/** The panel becomes the full page, or the full page becomes the panel. */
export function withFullPage(s: PageState, full: boolean): PageState {
  return { ...s, full: full ? true : undefined };
}

/** The record that the state names, or `null`. */
export function openedRecord(s: PageState): { object: string; id: string; tab: string | null; full: boolean } | null {
  return s.rec && s.ro ? { object: s.ro, id: s.rec, tab: s.rtab ?? null, full: s.full === true } : null;
}

/**
 * The start of the link that a table row or a board card carries: the current query with `ro` set,
 * and `rec=` last. The block appends the encoded id. The link is a query only, so it keeps the path.
 */
export function recordLinkBase(current: string, object: string): string {
  const p = new URLSearchParams(current);
  for (const k of ["rec", "ro", "rtab", "full"]) p.delete(k);
  p.set("ro", object);
  return `?${p.toString()}&rec=`;
}

/**
 * The `ro` and `rec` of a record link, or `null` when `href` is not one. The frame uses it to open
 * a link in the panel with no page load.
 */
export function parseRecordLink(href: string | null | undefined): { object: string; id: string } | null {
  if (typeof href !== "string" || !href.startsWith("?")) return null;
  const p = new URLSearchParams(href.slice(1));
  const object = p.get("ro");
  const id = p.get("rec");
  return object && id ? { object, id } : null;
}

// --- the link of a new child ------------------------------------------------

/**
 * A door that fills the link of a new child. "New" on a related table makes a child of the
 * record, so the relation field of the create holds `{ id: <record id> }`. The write still goes
 * through `door.run`, the one write path. The caller takes the field out of the form, so the member
 * never types the id, and a second value for the key cannot reach the door.
 */
export function linkedDoor(door: StudioDataDoor, field: string, recordId: string): StudioDataDoor {
  return {
    scope: door.scope,
    read: (source) => door.read(source),
    run: (action, args) => {
      const data = args && typeof args.data === "object" && args.data !== null && !Array.isArray(args.data)
        ? (args.data as Record<string, unknown>) : {};
      return door.run(action, { ...(args ?? {}), data: { ...data, [field]: { id: recordId } } });
    },
  };
}

// --- file values and the New button -----------------------------------------

/**
 * The words for a file value. The value is `{ id }` of an attachment, or a list of them. No Studio
 * route turns an id into a name or a download yet, so the member reads a count and never an id.
 * Empty when there is no file.
 */
export function fileLabel(value: unknown): string {
  const items = Array.isArray(value) ? value : value === null || value === undefined ? [] : [value];
  const n = items.filter((v) => typeof v === "object" && v !== null && typeof (v as { id?: unknown }).id === "string").length;
  return n === 0 ? "" : n === 1 ? "File attached" : `${n} files`;
}

/** What "New" on a related table does: `hidden` for a viewer, `off` with no declared create, else `on`. */
export function newButtonState(owner: boolean, createDeclared: boolean): "hidden" | "off" | "on" {
  return !owner ? "hidden" : createDeclared ? "on" : "off";
}
