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
 */
import type { FieldSpec } from "./fieldCheck";
import { validateRecord } from "./fieldCheck";
import type { ActionResult, Row, StudioDataDoor } from "./dataDoor";

/** One object with a field of each of the 16 types. */
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

const TAKEN = "Duplicate";
const OVER_LIMIT = "Over limit";

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
    async read() {
      await wait();
      return store.map((r) => ({ ...r }));
    },
    async run(action, args): Promise<ActionResult> {
      await wait();
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
