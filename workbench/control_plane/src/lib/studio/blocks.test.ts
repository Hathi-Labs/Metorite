/**
 * STU-D2a fence: the Table, Form and Detail blocks.
 * The vitest env has no DOM, so the views draw from props with
 * `renderToStaticMarkup`, and the form logic runs as pure functions.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/blocks-preview",
}));

import GenerativeUINode, { BoundStatus } from "@/components/GenerativeUINode";
import StudioDetail from "@/components/studio/StudioDetail";
import { FormView, controlId, errorIdOf } from "@/components/studio/StudioForm";
import { clearAll, put } from "@/lib/dataCache";
import {
  FIELD_TYPE_LIST, cellKindFor, displayValue, draftToValue, emptyDraft, fieldGrid, tableBlock, valueToDraft,
} from "./blocks";
import { StudioDataContext } from "./dataContext";
import { type Row, type StudioDataDoor, sourceKey } from "./dataDoor";
import { type FieldSpec, checkValue, validateRecord } from "./fieldCheck";
import { afterSend, blurProblem, checkForm, initialDrafts, recordOf, sendRecord, statusLines } from "./formModel";
import { SAMPLE_FIELDS, SAMPLE_ROWS, SAMPLE_TABLE_KEYS, makeSampleDoor } from "./sampleDoor";

beforeEach(() => clearAll());

const noop = () => {};
const form = (drafts = initialDrafts(SAMPLE_FIELDS), errors: Record<string, string> = {}) =>
  renderToStaticMarkup(createElement(FormView, {
    fields: SAMPLE_FIELDS, drafts, errors, formId: "f1", onDraft: noop, onBlur: noop, onSubmit: noop,
  }));

/** The opening tag of the element with this id. */
const tagOf = (html: string, id: string): string =>
  html.match(new RegExp("<[^>]*id=\"" + id + "\"[^>]*>"))?.[0] ?? "";

describe("the sample fields", () => {
  it("hold each of the 22 types", () => {
    expect(new Set(SAMPLE_FIELDS.map((f) => f.type))).toEqual(new Set(FIELD_TYPE_LIST));
  });
  it("the sample rows pass the check, so the Detail shows real values", () => {
    for (const row of SAMPLE_ROWS) {
      const { id, ...data } = row;
      expect(validateRecord(SAMPLE_FIELDS, data).ok, String(id)).toBe(true);
    }
  });
});

describe("Form: each type renders", () => {
  const html = form();
  for (const f of SAMPLE_FIELDS) {
    it(f.type, () => {
      expect(html).toContain("data-type=\"" + f.type + "\"");
      expect(tagOf(html, controlId("f1", f.key))).not.toBe("");
    });
  }
  it("uses the house controls and no raw colour", () => {
    expect(html).toContain("cc-control");
    expect(html).toContain("cc-checkbox");
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(/);
  });
  it("marks a required field", () => {
    expect(html).toContain("(required)");
  });
});

describe("Form: a bad value", () => {
  const drafts = { ...initialDrafts(SAMPLE_FIELDS), title: "", qty: "abc", contact: "nope" };
  const check = checkForm(SAMPLE_FIELDS, drafts);

  it("blocks the submit and names the first bad field in form order", () => {
    expect(check.ok).toBe(false);
    if (!check.ok) {
      expect(Object.keys(check.errors).sort()).toEqual(["contact", "qty", "title"]);
      expect(check.first).toBe("title");
    }
  });

  it("sets aria-invalid and aria-describedby on the control, and shows the sentence", () => {
    if (check.ok) throw new Error("expected errors");
    const html = form(drafts, check.errors);
    for (const key of ["title", "qty", "contact"]) {
      const tag = tagOf(html, controlId("f1", key));
      expect(tag, key).toContain("aria-invalid=\"true\"");
      expect(tag, key).toContain("aria-describedby=\"" + errorIdOf("f1", key) + "\"");
      expect(tagOf(html, errorIdOf("f1", key)), key).not.toBe("");
    }
  });

  it("a good field carries neither attribute", () => {
    const html = form(drafts, { title: "This field is required." });
    const tag = tagOf(html, controlId("f1", "qty"));
    expect(tag).not.toBe("");
    expect(tag).not.toContain("aria-invalid");
    expect(tag).not.toContain("aria-describedby");
  });

  it("checks one field on blur", () => {
    expect(blurProblem(SAMPLE_FIELDS, drafts, "contact")).toMatch(/email/i);
    expect(blurProblem(SAMPLE_FIELDS, drafts, "notes")).toBeNull();
    expect(blurProblem(SAMPLE_FIELDS, { ...drafts, title: "Ok" }, "title")).toBeNull();
  });

  it("refuses a blank required field", () => {
    expect(blurProblem(SAMPLE_FIELDS, initialDrafts(SAMPLE_FIELDS), "title")).toBe("This field is required.");
  });
});

describe("Form: the draft and the value", () => {
  const byKey = (k: string) => SAMPLE_FIELDS.find((f) => f.key === k) as FieldSpec;
  it("turns a record into drafts and back, with the same check result", () => {
    const { id: _id, ...data } = SAMPLE_ROWS[0];
    void _id;
    const back = recordOf(SAMPLE_FIELDS, initialDrafts(SAMPLE_FIELDS, data));
    const want = validateRecord(SAMPLE_FIELDS, data);
    const got = validateRecord(SAMPLE_FIELDS, back);
    expect(got.ok && want.ok).toBe(true);
    if (got.ok && want.ok) expect(got.value).toEqual(want.value);
  });
  it("reads an amount in major units as minor units", () => {
    expect(draftToValue(byKey("amount"), { amount: "1999.50", currency: "inr" }))
      .toEqual({ minor: 199950, currency: "INR" });
    const bad = draftToValue(byKey("amount"), { amount: "1.234", currency: "INR" }) as { minor: number };
    expect(Number.isNaN(bad.minor)).toBe(true);
  });
  it("keeps text that is no number as text, so the check refuses it", () => {
    expect(draftToValue(byKey("qty"), "12")).toBe(12);
    expect(draftToValue(byKey("qty"), "1e5")).toBe("1e5");
  });
  it("leaves a blank control out of the record", () => {
    const rec = recordOf(SAMPLE_FIELDS, initialDrafts(SAMPLE_FIELDS));
    expect(Object.keys(rec)).toEqual(["done"]);
    expect(emptyDraft(byKey("tags"))).toEqual([]);
    expect(valueToDraft(byKey("done"), true)).toBe(true);
  });
  it("takes one value on each line for a many field", () => {
    const f: FieldSpec = { key: "mails", type: "email", many: true };
    expect(draftToValue(f, "A@b.co\n\n c@d.io ")).toEqual(["A@b.co", "c@d.io"]);
    expect(checkForm([f], { mails: "A@b.co\nc@d.io" })).toEqual({ ok: true, value: { mails: ["a@b.co", "c@d.io"] } });
  });
});

describe("Form: the submit goes through door.run once", () => {
  function slowDoor() {
    const runs: unknown[][] = [];
    const door: StudioDataDoor = {
      scope: "t:draft",
      read: async () => [],
      run: (action, args) => {
        runs.push([action, args]);
        return new Promise((res) => setTimeout(() => res({ ok: true }), 5));
      },
    };
    return { door, runs };
  }

  it("a double submit calls door.run once (M3)", async () => {
    const { door, runs } = slowDoor();
    const busy = { current: false };
    const first = sendRecord(door, "object:visit.create", { title: "A" }, busy);
    const second = await sendRecord(door, "object:visit.create", { title: "A" }, busy);
    expect(second).toEqual({ kind: "busy" });
    expect(await first).toMatchObject({ kind: "sent", ok: true });
    expect(runs).toEqual([["object:visit.create", { data: { title: "A" } }]]);
    // The guard lifts after the run, so a later submit goes through.
    await sendRecord(door, "object:visit.create", { title: "B" }, busy);
    expect(runs).toHaveLength(2);
  });

  it("sends the id of an update beside the data", async () => {
    const { door, runs } = slowDoor();
    await sendRecord(door, "object:visit.update", { title: "A" }, { current: false }, { id: "v1" });
    expect(runs[0][1]).toEqual({ id: "v1", data: { title: "A" } });
  });

  it("returns a 422 as errors for its field", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    const out = await sendRecord(door, "object:visit.create", { title: "Duplicate" }, { current: false });
    expect(out).toMatchObject({ kind: "sent", ok: false });
    if (out.kind === "sent") expect(Object.keys(out.fieldErrors)).toEqual(["title"]);
  });

  it("answers a thrown door with a plain sentence", async () => {
    const door: StudioDataDoor = {
      scope: "t", read: async () => [], run: async () => { throw new Error("boom: secret"); },
    };
    const out = await sendRecord(door, "x", {}, { current: false });
    expect(out).toEqual({ kind: "sent", ok: false, message: "That did not work. Try again.", fieldErrors: {} });
  });
});

describe("Form: after a send", () => {
  const filled = { ...initialDrafts(SAMPLE_FIELDS), title: "A new visit", qty: "4" };
  const opts = { savedMessage: "Visit added", reset: true };

  it("a save that worked says so, empties the form and focuses the first field", () => {
    const out = afterSend(SAMPLE_FIELDS, filled, { kind: "sent", ok: true, message: null, fieldErrors: {} }, opts);
    expect(out.toast).toBe("Visit added");
    expect(out.drafts).toEqual(initialDrafts(SAMPLE_FIELDS));
    expect(out.drafts.title).toBe("");
    expect(out.focus).toBe("title");
    expect(out.errors).toEqual({});
  });

  it("a 422 keeps the values and shows no toast", () => {
    const out = afterSend(SAMPLE_FIELDS, filled,
      { kind: "sent", ok: false, message: "x", fieldErrors: { title: "Taken." } }, opts);
    expect(out.toast).toBeNull();
    expect(out.drafts).toBe(filled);
    expect(out.errors).toEqual({ title: "Taken." });
    expect(out.focus).toBe("title");
  });

  it("a failed run keeps the values and says why in the line", () => {
    const out = afterSend(SAMPLE_FIELDS, filled,
      { kind: "sent", ok: false, message: "That did not work. Try again.", fieldErrors: {} }, opts);
    expect(out.toast).toBeNull();
    expect(out.drafts).toBe(filled);
    expect(out.status).toEqual({ ok: false, text: "That did not work. Try again." });
  });

  it("an edit form keeps its values after a save", () => {
    const out = afterSend(SAMPLE_FIELDS, filled, { kind: "sent", ok: true, message: null, fieldErrors: {} },
      { ...opts, reset: false });
    expect(out.drafts).toBe(filled);
    expect(out.toast).toBe("Visit added");
  });
});

describe("Form: an edit", () => {
  const fields: FieldSpec[] = [
    { key: "title", type: "text", required: true },
    { key: "due", type: "date" },
    { key: "at", type: "datetime" },
  ];
  const initial = { title: "A", due: "2026-11-04", at: "2026-11-04T04:30:45Z" };

  it("sends null for an optional field that the member cleared", () => {
    const drafts = { ...initialDrafts(fields, initial), due: "" };
    const r = checkForm(fields, drafts, initial);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.due).toBeNull();
  });

  it("sends nothing for a field that was blank and stays blank", () => {
    const r = checkForm(fields, initialDrafts(fields, { title: "A" }), { title: "A" });
    expect(r.ok && "due" in r.value).toBe(false);
  });

  it("keeps the stored datetime, with its seconds, when the field is untouched", () => {
    const r = checkForm(fields, initialDrafts(fields, initial), initial);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.at).toBe("2026-11-04T04:30:45Z");
  });

  it("writes a changed datetime from the draft", () => {
    const drafts = { ...initialDrafts(fields, initial), at: "2026-11-05T10:00" };
    const r = checkForm(fields, drafts, initial);
    expect(r.ok).toBe(true);
    if (r.ok) expect(String(r.value.at)).toMatch(/^2026-11-0[45]T\d\d:\d\d:00Z$/);
  });
});

describe("Form: a field with no control", () => {
  it("a required many money field does not block the submit", () => {
    const fields: FieldSpec[] = [
      { key: "title", type: "text" },
      { key: "prices", type: "money", many: true, required: true },
    ];
    const r = checkForm(fields, initialDrafts(fields));
    expect(r.ok).toBe(true);
    const html = renderToStaticMarkup(createElement(FormView, {
      fields, drafts: initialDrafts(fields), errors: {}, formId: "g", onDraft: noop, onBlur: noop, onSubmit: noop,
    }));
    expect(html).toContain("has no editor yet");
  });
});

describe("Form: the money message", () => {
  it("names the decimals, not minor units", () => {
    const f: FieldSpec = { key: "m", type: "money" };
    const bad = draftToValue(f, { amount: "1.005", currency: "INR" });
    const r = checkValue(f, bad);
    expect(r).toEqual({ ok: false, message: "Enter an amount with at most 2 decimals." });
    const e = checkValue(f, draftToValue(f, { amount: "1e3", currency: "INR" }));
    expect(e.ok).toBe(false);
    const jpy = checkValue(f, draftToValue(f, { amount: "1.5", currency: "JPY" }));
    expect(jpy).toEqual({ ok: false, message: "Enter a whole amount, with no decimals." });
  });
});

describe("Form: the refusal contract", () => {
  const fields: FieldSpec[] = [{ key: "title", type: "text" }];
  const drafts = { title: "x" };
  const opts = { savedMessage: "Added", reset: true };

  it("a rule refusal shows in the status line and keeps the values", () => {
    const out = afterSend(fields, drafts,
      { kind: "sent", ok: false, message: "A day holds at most 2.", fieldErrors: {}, rule: "stock_not_negative" }, opts);
    expect(out.status?.text).toBe("Refused by stock_not_negative: A day holds at most 2.");
    expect(out.errors).toEqual({});
    expect(out.drafts).toBe(drafts);
  });

  it("a fieldErrors key that names no field joins the status line", () => {
    const out = afterSend(fields, drafts,
      { kind: "sent", ok: false, message: "No.", fieldErrors: { title: "Taken.", ghost: "Bad." } }, opts);
    expect(out.errors).toEqual({ title: "Taken." });
    expect(out.status?.text).toBe("ghost: Bad.");
  });

  it("a field error alone leaves the status line empty", () => {
    expect(statusLines(fields, { message: "x", fieldErrors: { title: "Taken." } })).toEqual([]);
  });

  it("the sample door answers a rule refusal and a field refusal", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    const rule = await sendRecord(door, "object:visit.create", { title: "Over limit" }, { current: false });
    expect(rule).toMatchObject({ kind: "sent", ok: false, rule: "visit_limit" });
    if (rule.kind === "sent") expect(rule.message).toContain("at most 2");
    const field = await sendRecord(door, "object:visit.create", { title: "Duplicate" }, { current: false });
    expect(field).toMatchObject({ kind: "sent", ok: false, fieldErrors: { title: expect.any(String) } });
  });
});

describe("Detail: each type renders", () => {
  const html = renderToStaticMarkup(createElement(StudioDetail, { fields: SAMPLE_FIELDS, record: SAMPLE_ROWS[0] }));
  for (const f of SAMPLE_FIELDS) {
    it(f.type, () => expect(html).toContain("data-type=\"" + f.type + "\""));
  }
  it("shows the words of the values", () => {
    expect(html).toContain("Install the press");
    expect(html).toContain("12,500.00");
    expect(html).toContain("Planned");
    expect(html).toContain("Urgent");
    expect(html).toContain("18.5204, 73.8567");
    expect(html).toContain("href=\"https://example.com/sites/pune\"");
  });
  it("shows a dash for a missing value", () => {
    const sparse = renderToStaticMarkup(createElement(StudioDetail, { fields: SAMPLE_FIELDS, record: { title: "Only a title" } }));
    expect(sparse).toContain("—");
  });
  it("does not link an address that fails the check", () => {
    const unsafe = renderToStaticMarkup(createElement(StudioDetail, {
      fields: [{ key: "site", type: "url" }], record: { site: "javascript:alert(1)" },
    }));
    expect(unsafe).not.toContain("<a ");
  });
});

describe("Table: the D1 bind path with field types", () => {
  const fields = SAMPLE_FIELDS.filter((f) => SAMPLE_TABLE_KEYS.includes(f.key));
  const source = { action: "object:visit.list" };
  const node = tableBlock(fields, source, "Visits");
  const door: StudioDataDoor = { scope: "app1:draft", read: async () => [], run: async () => ({ ok: true }) };
  const draw = (n: unknown) =>
    renderToStaticMarkup(createElement(StudioDataContext.Provider, { value: door },
      createElement(GenerativeUINode, { spec: n, onAction: noop })));

  it("shows loading while the cache is empty", () => {
    const html = draw(node);
    expect(html).toContain("aria-busy=\"true\"");
    expect(html).not.toContain("<table");
  });
  it("shows empty when the door read no rows", () => {
    put<Row[]>(sourceKey(door, source), []);
    expect(draw(node)).toContain("No Visits yet");
  });
  it("shows the error state with a way back", () => {
    const html = renderToStaticMarkup(createElement(BoundStatus, { view: "error", onRetry: noop, title: "Visits" }));
    expect(html).toContain("Could not load this list.");
    expect(html).toContain("Try again");
  });
  it("shows the rows with headers from the labels and a filter box", () => {
    put<Row[]>(sourceKey(door, source), SAMPLE_ROWS);
    const html = draw(node);
    expect(html).toContain("Install the press");
    expect(html).toContain("Calibrate the line");
    expect(html).toContain("Quote");
    expect(html).toContain("12,500.00");
    expect(html).toContain("aria-label=\"Filter rows\"");
  });
  it("takes the cell kinds from the field types", () => {
    expect(cellKindFor("date")).toBe("text");
    expect(cellKindFor("select")).toBe("tag");
    expect(cellKindFor("person")).toBe("person");
    expect(cellKindFor("money")).toBe("text");
    const g = fieldGrid(fields, SAMPLE_ROWS);
    expect(g.kinds).toEqual(fields.map((f) => cellKindFor(f.type)));
    expect(g.rows[0].cells[0]).toBe("Install the press");
  });
  it("words each type", () => {
    const f = (type: FieldSpec["type"]): FieldSpec => ({ key: "k", type });
    expect(displayValue(f("checkbox"), true)).toBe("Yes");
    expect(displayValue(f("multiSelect"), ["A", "B"])).toBe("A, B");
    expect(displayValue(f("relation"), { id: "c-1" })).toBe("c-1");
    expect(displayValue(f("text"), null)).toBe("");
  });
  it("a grid with no filterable flag draws no filter box", () => {
    put<Row[]>(sourceKey(door, source), SAMPLE_ROWS);
    const plain = { type: "template", props: { name: "dataGrid", source, data: { fields } } };
    expect(draw(plain)).not.toContain("Filter rows");
  });
});

describe("the Table and the Detail read a value the same way", () => {
  const door: StudioDataDoor = { scope: "same:draft", read: async () => [], run: async () => ({ ok: true }) };
  const source = { action: "object:visit.list" };
  const drawTable = (f: FieldSpec, row: Row) => {
    clearAll();
    put<Row[]>(sourceKey(door, source), [row]);
    return renderToStaticMarkup(createElement(StudioDataContext.Provider, { value: door },
      createElement(GenerativeUINode, { spec: tableBlock([f], source), onAction: noop })));
  };
  const drawDetail = (f: FieldSpec, row: Row) =>
    renderToStaticMarkup(createElement(StudioDetail, { fields: [f], record: row }));
  const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // A rich text is plain words in the Table and rendered markdown in the Detail.
  for (const f of SAMPLE_FIELDS.filter((x) => x.type !== "richText")) {
    it(f.type + ": one text in both blocks", () => {
      const row = { id: "r1", [f.key]: SAMPLE_ROWS[0][f.key] };
      const want = esc(displayValue(f, row[f.key]));
      expect(want).not.toBe("");
      // A multi value is words in the Detail and a chip each in the Table, so
      // each item is looked for on its own.
      const parts = Array.isArray(row[f.key]) ? (row[f.key] as string[]) : [want];
      const table = drawTable(f, row);
      const detail = drawDetail(f, row);
      for (const part of parts) {
        expect(table, "table " + f.key).toContain(esc(String(part)));
        expect(detail, "detail " + f.key).toContain(esc(String(part)));
      }
    });
  }

  it("richText: plain words in the Table, markdown elements in the Detail", () => {
    const f = SAMPLE_FIELDS.find((x) => x.type === "richText") as FieldSpec;
    const row = { id: "r1", [f.key]: SAMPLE_ROWS[0][f.key] };
    const table = drawTable(f, row);
    const detail = drawDetail(f, row);
    expect(table).toContain("Findings The base plate is level.");
    expect(table).not.toContain("**");
    expect(detail).toMatch(/<strong[^>]*>base plate<\/strong>/);
    expect(detail).toMatch(/<li[^>]*>Anchor bolts: set<\/li>/);
  });

  it("richText: a tag in the text is not drawn as an element", () => {
    const f: FieldSpec = { key: "r", type: "richText" };
    const html = drawDetail(f, { id: "r1", r: "Hi <script>alert(1)</script> there" });
    expect(html).not.toContain("<script");
  });

  it("words a date as a day, and no raw ISO text shows in either block", () => {
    const f: FieldSpec = { key: "d", type: "date" };
    const row = { id: "r1", d: "2026-11-04" };
    expect(displayValue(f, "2026-11-04")).toBe("4 Nov 2026");
    expect(drawTable(f, row)).toContain("4 Nov 2026");
    expect(drawDetail(f, row)).toContain("4 Nov 2026");
    expect(drawDetail(f, row)).not.toContain("2026-11-04");
  });

  it("words a datetime in local time, with the time", () => {
    const f: FieldSpec = { key: "t", type: "datetime" };
    const d = new Date("2026-11-04T04:30:00Z");
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const text = displayValue(f, "2026-11-04T04:30:00Z");
    expect(text).toMatch(/^\d{1,2} \w{3} \d{4}, \d{2}:\d{2}$/);
    expect(text.endsWith(", " + hh + ":" + mm)).toBe(true);
    expect(drawDetail(f, { t: "2026-11-04T04:30:00Z" })).not.toContain("T04:30:00Z");
  });

  it("draws a select and a person as the same chip in both blocks", () => {
    for (const [type, value] of [["select", "Planned"], ["multiSelect", ["Urgent", "Audit"]], ["person", "asha@example.com"]] as const) {
      const f: FieldSpec = { key: "k", type, options: ["Planned", "Urgent", "Audit"] };
      const row = { id: "r1", k: value };
      const chips = (html: string) => (html.match(/(?:border-cat-\d+\/30|bg-secondary text-muted-foreground px-1)/g) ?? []).sort();
      const t = chips(drawTable(f, row));
      expect(t.length, type).toBeGreaterThan(0);
      expect(chips(drawDetail(f, row)), type).toEqual(t);
    }
  });
});
