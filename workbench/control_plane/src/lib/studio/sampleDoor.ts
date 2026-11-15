/**
 * The sample data and the fake door of the block preview (STU-D2a).
 *
 * `/studio/blocks-preview` mounts the Table, Form and Detail on these. No
 * network call and no stored record: the door keeps its rows in memory, and
 * a reload brings the first rows back. It is NOT the real door. The live
 * door of an object is B2b and B2c (`ai_studio.md` §14 D2b).
 *
 * Its `run` plays the server: it checks a record with the same check as the
 * client, and it refuses one more rule that only a server can know, a title
 * that is taken. That refusal shows a 422 under its field.
 *
 * The Gallery (STU-D19a) draws on sample assets. Its door gives the address of
 * a picture that ships in this repo (`fileAddress`). No real door has one.
 */
import type { FieldSpec } from "./fieldCheck";
import { validateRecord } from "./fieldCheck";
import type { ActionResult, Row, StudioDataDoor } from "./dataDoor";
import { sampleRowsFor } from "./sampleRows";

/** One object with a field of each of the 22 types. */
export const SAMPLE_FIELDS: FieldSpec[] = [
  { key: "title", type: "text", label: "Title", required: true },
  { key: "notes", type: "longText", label: "Notes" },
  { key: "qty", type: "number", label: "Pallets" },
  { key: "amount", type: "money", label: "Quote" },
  { key: "visit_date", type: "date", label: "Visit date" },
  { key: "starts_at", type: "datetime", label: "Starts at" },
  { key: "done", type: "checkbox", label: "Done" },
  { key: "status", type: "select", label: "Status", options: ["Planned", "On site", "Closed"] },
  { key: "tags", type: "multiSelect", label: "Tags", options: ["Urgent", "Repeat", "Audit"] },
  { key: "owner", type: "person", label: "Owner" },
  { key: "customer", type: "relation", label: "Customer", to: "customer" },
  { key: "photo", type: "file", label: "Photo" },
  { key: "site", type: "url", label: "Site" },
  { key: "contact", type: "email", label: "Contact" },
  { key: "phone", type: "phone", label: "Phone" },
  { key: "location", type: "geo", label: "Location" },
  { key: "report", type: "richText", label: "Site report" },
  { key: "score", type: "rating", label: "Customer score" },
  { key: "duration", type: "duration", label: "Time on site" },
  { key: "weight", type: "quantity", label: "Load", units: ["kg", "t"] },
  { key: "discount", type: "percent", label: "Discount" },
  { key: "serial", type: "barcode", label: "Serial", format: "ean13" },
];

/** The columns the Table shows. The Form and the Detail show all of the fields. */
export const SAMPLE_TABLE_KEYS = ["title", "status", "owner", "visit_date", "amount", "tags", "done"];

export const SAMPLE_ROWS: Row[] = [
  {
    id: "v1", title: "Install the press", notes: "Bring the second crane.\nGate code at the desk.",
    qty: 12, amount: { minor: 1250000, currency: "INR" }, visit_date: "2026-11-04",
    starts_at: "2026-11-04T04:30:00Z", done: false, status: "Planned", tags: ["Urgent", "Audit"],
    owner: "asha@example.com", customer: { id: "cust-104" },
    photo: { id: "123e4567-e89b-12d3-a456-426614174000" }, site: "https://example.com/sites/pune",
    contact: "ops@example.com", phone: "+919876543210", location: { lat: 18.5204, lng: 73.8567 },
    report: "## Findings\n\nThe **base plate** is level.\n\n- Anchor bolts: set\n- Power: on site",
    score: 4, duration: 5400, weight: { value: "1250.5", unit: "kg" }, discount: 1250,
    serial: "4006381333931",
  },
  {
    id: "v2", title: "Calibrate the line", qty: 3, amount: { minor: 480050, currency: "INR" },
    visit_date: "2026-11-09", done: true, status: "Closed", tags: ["Repeat"],
    owner: "ravi@example.com", site: "https://example.com/sites/nashik",
  },
  {
    id: "v3", title: "Train the night shift", visit_date: "2026-11-12", done: false, status: "On site",
    owner: "asha@example.com", tags: [],
  },
];

// --- the Gallery (STU-D19a) ----------------------------------------------------

/**
 * The pictures of the sample files. Each one ships in this repo (`public/office-objects`), so
 * the preview loads nothing from another site. The last digit of a sample file id picks one.
 */
export const SAMPLE_PICTURES: readonly string[] = [
  "/office-objects/printer-3d/south.png",
  "/office-objects/desk-computer/south.png",
  "/office-objects/coffee-machine/south.png",
  "/office-objects/filing-cabinet/south.png",
  "/office-objects/printer-office/south.png",
  "/office-objects/water-cooler/south.png",
  "/office-objects/air-conditioner/south.png",
  "/office-objects/tv-screen/south.png",
  "/office-objects/workstation/south.png",
  "/office-objects/printer-3d-large/south.png",
];

/**
 * The image address of a sample file, by the last digit of its id. Null for an id that ends in
 * no digit. ⚠️ The sample door is the ONLY door with a file address in D19a.
 */
export function sampleFileAddress(fileId: string): string | null {
  const m = /([0-9])$/.exec(fileId);
  return m ? (SAMPLE_PICTURES[Number(m[1])] ?? null) : null;
}

/** The object of the Gallery preview: the assets of an office, each with a photo. */
export const SAMPLE_ASSET_FIELDS: FieldSpec[] = [
  { key: "name", type: "text", label: "Asset", required: true },
  { key: "status", type: "select", label: "Status", options: ["Active", "On hold", "Retired"] },
  { key: "owner", type: "person", label: "Owner" },
  { key: "photo", type: "file", label: "Photo" },
  { key: "bought_on", type: "date", label: "Bought on" },
];

/** Every ninth sample asset has no photo, so its card shows the placeholder. */
export const NO_PHOTO_EVERY = 9;

/** `count` sample assets, with the ids `s1`, `s2` and on. */
export function sampleAssetRows(count: number): Row[] {
  return sampleRowsFor(SAMPLE_ASSET_FIELDS, count).map((row, i) => {
    if ((i + 1) % NO_PHOTO_EVERY !== 0) return row;
    const { photo: _photo, ...rest } = row;
    void _photo;
    return rest;
  });
}

type Measure = { op: "count" | "sum" | "average"; field?: string };

/**
 * The aggregate of the sample rows, in the wire shape of C2b (`objects_store.AggregateResult`).
 * The preview calls no live door, so this plays the server for `object:visit.aggregate`: a group
 * by one field (a date by day or by month), and a count, a sum or an average of each group.
 */
export function sampleAggregate(rows: readonly Row[], args: Record<string, unknown> | undefined): Row {
  const g = args?.group_by as { field?: string; bucket?: string } | undefined;
  const ms = (Array.isArray(args?.measures) && args.measures.length ? args.measures : [{ op: "count" }]) as Measure[];
  const keyOf = (r: Row): unknown => {
    const v = g?.field ? r[g.field] : null;
    if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v) && g?.bucket === "month") return v.slice(0, 7);
    return v === undefined || v === "" ? null : v;
  };
  const cell = (rs: readonly Row[], m: Measure): number | null => {
    if (m.op === "count" || !m.field) return rs.length;
    const xs = rs.map((r) => r[m.field as string]).map((v) => (v && typeof v === "object" ? (v as { minor?: number }).minor : v))
      .filter((v): v is number => typeof v === "number");
    if (m.op === "sum") return xs.reduce((a, b) => a + b, 0);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  };
  const make = (key: unknown, rs: readonly Row[]) => ({ key, count: rs.length, values: ms.map((m) => cell(rs, m)) });
  const by = new Map<unknown, Row[]>();
  if (g?.field) for (const r of rows) by.set(keyOf(r), [...(by.get(keyOf(r)) ?? []), r]);
  const money = ms.map((m) => (m.field ? rows.map((r) => r[m.field as string]).find((v) => v && typeof v === "object") : undefined));
  return {
    measures: ms.map((m) => (m.field ? { op: m.op, field: m.field } : { op: m.op })),
    currencies: money.map((v) => ((v as { currency?: string } | undefined)?.currency ?? null)),
    groups: [...by.entries()].sort((a, b) => String(a[0]).localeCompare(String(b[0]))).map(([k, rs]) => make(k, rs)),
    other: null, folded: 0, total: make(null, rows),
  };
}

const TAKEN = "Duplicate";
const OVER_LIMIT = "Over limit";
const CLOSED = "Closed";

/** A fake door over `rows`. `delayMs` shows the pending state of a button. */
export function makeSampleDoor(
  fields: readonly FieldSpec[] = SAMPLE_FIELDS,
  rows: Row[] = SAMPLE_ROWS,
  delayMs = 350,
): StudioDataDoor {
  const store: Row[] = rows.map((r) => ({ ...r }));
  const wait = () => new Promise<void>((r) => setTimeout(r, delayMs));
  let next = store.length + 1;
  return {
    scope: "sample:draft",
    // The pictures of the sample files, for the Gallery (STU-D19a). No real door has this.
    fileAddress: sampleFileAddress,
    async read(source) {
      await wait();
      if (source?.action.endsWith(".aggregate")) return [sampleAggregate(store, source.args)];
      return store.map((r) => ({ ...r }));
    },
    async run(action, args): Promise<ActionResult> {
      await wait();
      // A patch: an update that carries an id and lacks a required field. A
      // Board sends `{id, data: {status}}`. The patch joins the stored record
      // before the check, and a null clears the field. The Form sends whole
      // records, and its own check blocks a missing required field first.
      const at0 = action.endsWith(".update") ? store.findIndex((r) => r.id === args?.id) : -1;
      const patch = args?.data;
      if (
        at0 >= 0 && patch && typeof patch === "object" && !Array.isArray(patch)
        && fields.some((f) => f.required && !(f.key in patch))
      ) {
        const { id: _id, ...rest } = store[at0];
        void _id;
        const joined: Record<string, unknown> = { ...rest, ...(patch as Record<string, unknown>) };
        for (const k of Object.keys(joined)) if (joined[k] === null) delete joined[k];
        const check = validateRecord(fields, joined);
        if (!check.ok) {
          const fieldErrors = Object.fromEntries(check.problems.map((p) => [p.field, p.message]));
          return { ok: false, message: "Fix the marked fields.", data: { fieldErrors } };
        }
        if (check.value.status === CLOSED && check.value.done !== true) {
          return {
            ok: false, message: "That did not save.",
            data: { rule: "close_needs_done", message: "A visit closes only when it is done." },
          };
        }
        store[at0] = { id: store[at0].id, ...check.value };
        return { ok: true, message: "Saved to the sample list. Nothing is stored." };
      }
      const checked = validateRecord(fields, args?.data);
      if (!checked.ok) {
        const fieldErrors = Object.fromEntries(checked.problems.map((p) => [p.field, p.message]));
        return { ok: false, message: "Fix the marked fields.", data: { fieldErrors } };
      }
      if (checked.value.title === OVER_LIMIT) {
        // A rule refusal: no field is to blame (a RuleViolation of the store).
        return {
          ok: false, message: "That did not save.",
          data: { rule: "visit_limit", message: "A day holds at most 2 visits. Pick another day." },
        };
      }
      if (checked.value.title === TAKEN) {
        return {
          ok: false, message: "That did not save.",
          data: { fieldErrors: { title: "A visit with this title exists. Use another title." } },
        };
      }
      if (action.endsWith(".update")) {
        const at = store.findIndex((r) => r.id === args?.id);
        if (at < 0) return { ok: false, message: "That record is gone." };
        store[at] = { id: store[at].id, ...checked.value };
        return { ok: true, message: "Saved to the sample list. Nothing is stored." };
      }
      store.push({ id: `v${next++}`, ...checked.value });
      return { ok: true, message: "Added to the sample list. Nothing is stored." };
    },
  };
}
