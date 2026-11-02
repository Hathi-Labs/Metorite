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
import { type FieldSpec, validateRecord } from "./fieldCheck";
import { blurProblem, checkForm, initialDrafts, recordOf, sendRecord } from "./formModel";
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
  it("hold each of the 16 types", () => {
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
    let release: () => void = () => {};
    const door: StudioDataDoor = {
      scope: "t:draft",
      read: async () => [],
      run: (action, args) => {
        runs.push([action, args]);
        return new Promise((res) => { release = () => res({ ok: true }); });
      },
    };
    return { door, runs, release: () => release() };
  }

  it("a double submit calls door.run once (M3)", async () => {
    const { door, runs, release } = slowDoor();
    const busy = { current: false };
    const first = sendRecord(door, "object:visit.create", { title: "A" }, busy);
    const second = await sendRecord(door, "object:visit.create", { title: "A" }, busy);
    expect(second).toEqual({ kind: "busy" });
    release();
    expect(await first).toMatchObject({ kind: "sent", ok: true });
    expect(runs).toEqual([["object:visit.create", { data: { title: "A" } }]]);
    // The guard lifts after the run, so a later submit goes through.
    const again = sendRecord(door, "object:visit.create", { title: "B" }, busy);
    release();
    await again;
    expect(runs).toHaveLength(2);
  });

  it("sends the id of an update beside the data", async () => {
    const { door, runs, release } = slowDoor();
    const p = sendRecord(door, "object:visit.update", { title: "A" }, { current: false }, { id: "v1" });
    release();
    await p;
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
    expect(cellKindFor("date")).toBe("date");
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
