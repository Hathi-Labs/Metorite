// The field studio, the shared client (WS-53 CRM-C4p, spec section 20.7).
//
// The runner has no DOM, so each component is rendered to a string. A hook
// cannot run here, so `useFieldLayout` is tested through `readFieldLayout`,
// its read, against a stubbed fetch. An edit cannot run either, so the value
// that an edit sends is tested through `toWire`, and a source check proves
// that each text edit of `FieldInput` goes through it.
//
// `tests/unit/test_crm_field_input_parity.py` holds done-when item 8, and
// `crmX4.test.ts` holds item 9 (no raw control under `app/crm`).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { LOCK_TIP } from "../lib/connect";
import {
  EMAIL_MAX,
  FIELD_TYPES,
  NUMBER_LIMIT,
  PHONE_MAX,
  TEXTAREA_MAX,
  TEXT_MAX,
  URL_MAX,
  checkValue,
  fromWire,
  isDateTimeValue,
  lookupValue,
  textLength,
  toWire,
  type FieldType,
} from "../lib/fieldLayout";
import { fieldText, formatField, LOOKUP_MISSING, type FieldDef } from "../lib/fields";
import { readFieldLayout } from "../lib/useCrmMode";
import FieldInput from "./FieldInput";
import FieldValue from "./FieldValue";

const noop = () => undefined;

const OPTIONS = [
  { label: "Gold", value: "gold" },
  { label: "Silver", value: "silver" },
  { label: "Bronze", value: "bronze", retired: true },
];

const def = (type: string, over: Partial<FieldDef> = {}): FieldDef => ({
  key: `f_${type}`,
  label: `My ${type}`,
  type,
  options: type === "select" || type === "multiselect" ? OPTIONS : null,
  position: 0,
  visible: true,
  source: "native",
  api_name: null,
  lookup_target: type === "lookup" ? "contacts" : null,
  ...over,
});

const draw = (field: FieldDef, value: unknown, over: Record<string, unknown> = {}) =>
  renderToStaticMarkup(createElement(FieldInput, { def: field, value, onChange: noop, ...over }));

const ID = "0b9c2f9e-4a8e-4b8f-9c2d-1f2e3d4c5b6a";

// ── Item 1: the primitive of each type ─────────────────────────────────────

/** What the markup of each primitive holds. */
const INPUT = /<input(?![^>]*type="checkbox")[^>]*class="cc-control/;
const TEXTAREA = /<textarea[^>]*class="cc-control/;
const SELECT_BUTTON = /<button[^>]*aria-haspopup="listbox"/;
const CHECKBOX = /<input[^>]*type="checkbox"[^>]*class="cc-checkbox/;

const PRIMITIVE: Record<FieldType, { has: RegExp[]; attr?: string; value: unknown }> = {
  text: { has: [INPUT], value: "Hello" },
  textarea: { has: [TEXTAREA], value: "One\nTwo" },
  number: { has: [INPUT], attr: 'inputMode="numeric"', value: 12 },
  decimal: { has: [INPUT], attr: 'inputMode="decimal"', value: 1.5 },
  currency: { has: [INPUT], attr: 'inputMode="decimal"', value: 1000 },
  percent: { has: [INPUT], attr: 'inputMode="decimal"', value: 12.5 },
  date: { has: [INPUT], attr: 'type="date"', value: "2026-10-14" },
  datetime: { has: [INPUT], attr: 'type="datetime-local"', value: "2026-10-14T05:00:00.000Z" },
  boolean: { has: [CHECKBOX], value: true },
  email: { has: [INPUT], attr: 'type="email"', value: "a@b.in" },
  phone: { has: [INPUT], attr: 'type="tel"', value: "+91 98450 12345" },
  url: { has: [INPUT], attr: 'type="url"', value: "https://example.com" },
  select: { has: [SELECT_BUTTON], value: "gold" },
  multiselect: { has: [CHECKBOX], value: ["gold"] },
  lookup: { has: [INPUT, SELECT_BUTTON], value: { id: ID, name: "Asha Rao" } },
};

describe("FieldInput draws the primitive of each type (CRM-C4p item 1)", () => {
  it("covers each of the 15 types of the studio", () => {
    expect(Object.keys(PRIMITIVE).sort()).toEqual([...FIELD_TYPES].sort());
    expect(FIELD_TYPES).toHaveLength(15);
  });

  for (const type of FIELD_TYPES) {
    it(`draws ${type} with its primitive`, () => {
      const row = PRIMITIVE[type];
      const html = draw(def(type), row.value);
      for (const marker of row.has) expect(html).toMatch(marker);
      if (row.attr) expect(html).toContain(row.attr);
      // A text input of another type is not this row's input.
      if (!row.has.includes(TEXTAREA)) expect(html).not.toMatch(TEXTAREA);
    });
  }

  it("draws one checkbox for each option a multi-select offers", () => {
    const html = draw(def("multiselect"), ["gold"]);
    expect(html.match(/cc-checkbox/g)).toHaveLength(2);
    expect(html).toContain("Gold");
    expect(html).toContain("Silver");
    // Bronze is retired, and this record does not hold it.
    expect(html).not.toContain("Bronze");
  });

  it("keeps a retired option that the record holds as a choice", () => {
    const html = draw(def("multiselect"), ["bronze"]);
    expect(html).toContain("Bronze");
    expect(html.match(/cc-checkbox/g)).toHaveLength(3);
  });

  it("shows the option label of a select on its button", () => {
    expect(draw(def("select"), "gold")).toContain("Gold");
    expect(draw(def("select"), "bronze")).toContain("Bronze");
  });

  it("shows the stored name of a lookup on its button", () => {
    expect(draw(def("lookup"), { id: ID, name: "Asha Rao" })).toContain("Asha Rao");
  });

  it("shows the error under the input, and the help text", () => {
    const html = draw(def("text", { help_text: "As on the card" }), "x", { error: "Give text." });
    expect(html).toContain('role="alert"');
    expect(html).toContain("Give text.");
    expect(html).toContain("As on the card");
    expect(html).toContain('aria-invalid="true"');
    expect(html.indexOf("cc-control")).toBeLessThan(html.indexOf("Give text."));
  });

  it("shows the value of a datetime in local time, with no zone", () => {
    const text = fromWire(def("datetime"), "2026-10-14T05:00:00.000Z");
    expect(text).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(toWire(def("datetime"), text)).toBe("2026-10-14T05:00:00.000Z");
  });
});

// ── Item 2: the client check of each type ──────────────────────────────────

const GOOD_BAD: Record<FieldType, { good: unknown[]; bad: unknown[] }> = {
  text: { good: ["Hello"], bad: [12, true] },
  textarea: { good: ["One\nTwo"], bad: [12] },
  number: { good: [12, -3, 0], bad: [1.5, "12", Number.NaN, true] },
  decimal: { good: [1.5, -2, 0], bad: ["1.5", Number.POSITIVE_INFINITY, false] },
  currency: { good: [120000.5], bad: ["120000"] },
  percent: { good: [12.5], bad: ["12%"] },
  date: { good: ["2026-10-14", "2024-02-29"], bad: ["2026-02-29", "2026-13-01", "14-10-2026", "20261014"] },
  datetime: {
    good: ["2026-10-14T05:00:00.000Z", "2026-10-14T10:30+05:30"],
    bad: ["2026-10-14T10:30", "2026-10-14T24:00Z", "2026-02-30T10:00Z", "2026-10-14 10:30Z"],
  },
  boolean: { good: [true, false], bad: ["true", 1] },
  email: { good: ["a@b.in"], bad: ["a@b", "a@b.com?bcc=x", "a,b@c.in"] },
  phone: {
    good: ["+91 98450 12345", "1234", "+91 98450 12345 ext. 56", "080 4123 4567 #2"],
    bad: ["123", "abc", "+91+98450", "12/34/56"],
  },
  url: { good: ["https://example.com", "HTTP://X.IN"], bad: ["ftp://x.in", "javascript:alert(1)", "https://a b"] },
  select: { good: ["gold"], bad: ["bronze", "platinum", ["gold"]] },
  multiselect: { good: [["gold", "silver"]], bad: [["gold", "gold"], ["bronze"], "gold"] },
  lookup: { good: [{ id: ID, name: "Asha" }], bad: [{ id: "12", name: "x" }, { name: "x" }, ID] },
};

describe("checkValue passes a good value and refuses a bad one (CRM-C4p item 2)", () => {
  for (const type of FIELD_TYPES) {
    it(`checks a ${type}`, () => {
      const field = def(type);
      for (const good of GOOD_BAD[type].good) expect(checkValue(field, good), String(good)).toBeNull();
      for (const bad of GOOD_BAD[type].bad) expect(checkValue(field, bad), String(bad)).not.toBeNull();
    });
  }

  it("lets an empty value through, and refuses it on a required field", () => {
    for (const empty of [null, undefined, "", "   ", [], {}]) {
      expect(checkValue(def("text"), empty)).toBeNull();
      expect(checkValue(def("text", { required: true }), empty)).toMatch(/required/);
    }
  });

  it("allows a retired option that the record holds, as _select does", () => {
    expect(checkValue(def("select"), "bronze", "bronze")).toBeNull();
    expect(checkValue(def("multiselect"), ["gold", "bronze"], ["bronze"])).toBeNull();
  });

  it("refuses a lookup with no Native target", () => {
    expect(checkValue(def("lookup", { lookup_target: "module:Products" }), { id: ID })).toMatch(/target/);
  });
});

describe("each limit holds at its edge (CRM-C4p item 2)", () => {
  const at = (n: number, unit = "a") => unit.repeat(n);

  it("text: 255 characters", () => {
    expect(TEXT_MAX).toBe(255);
    expect(checkValue(def("text"), at(255))).toBeNull();
    expect(checkValue(def("text"), at(256))).toMatch(/255/);
  });

  it("textarea: 32 000 characters", () => {
    expect(TEXTAREA_MAX).toBe(32_000);
    expect(checkValue(def("textarea"), at(32_000))).toBeNull();
    expect(checkValue(def("textarea"), at(32_001))).toMatch(/32000/);
  });

  it("a number: less than 10^15", () => {
    expect(NUMBER_LIMIT).toBe(1e15);
    for (const type of ["number", "decimal", "currency", "percent"]) {
      expect(checkValue(def(type), 999_999_999_999_999)).toBeNull();
      expect(checkValue(def(type), -999_999_999_999_999)).toBeNull();
      expect(checkValue(def(type), 1e15)).toMatch(/10\^15/);
      expect(checkValue(def(type), -1e15)).toMatch(/10\^15/);
    }
  });

  it("email: 254 characters", () => {
    expect(EMAIL_MAX).toBe(254);
    const local = (n: number) => `${at(n - "@b.in".length)}@b.in`;
    expect(checkValue(def("email"), local(254))).toBeNull();
    expect(checkValue(def("email"), local(255))).toMatch(/254/);
  });

  it("phone: 40 characters", () => {
    expect(PHONE_MAX).toBe(40);
    expect(checkValue(def("phone"), at(40, "1"))).toBeNull();
    expect(checkValue(def("phone"), at(41, "1"))).toMatch(/40/);
  });

  it("url: 2048 characters", () => {
    expect(URL_MAX).toBe(2048);
    const url = (n: number) => `https://x.in/${at(n - "https://x.in/".length)}`;
    expect(checkValue(def("url"), url(2048))).toBeNull();
    expect(checkValue(def("url"), url(2049))).toMatch(/2048/);
  });

  it("counts code points, as Python len() does, not UTF-16 units", () => {
    const emoji = "😀";
    expect(emoji.length).toBe(2);
    expect(textLength(emoji)).toBe(1);
    expect(checkValue(def("text"), at(255, emoji))).toBeNull();
    expect(checkValue(def("text"), at(256, emoji))).toMatch(/255/);
  });

  it("holds the year of a date and a time in 1..9999, as Python does", () => {
    expect(checkValue(def("date"), "0000-01-01")).not.toBeNull();
    expect(checkValue(def("date"), "0001-01-01")).toBeNull();
    expect(checkValue(def("date"), "9999-12-31")).toBeNull();
    // `toISOString()` writes these two years, and the gateway refuses both.
    expect(new Date(Date.UTC(10000, 0, 1)).toISOString()).toBe("+010000-01-01T00:00:00.000Z");
    expect(isDateTimeValue("+010000-01-01T00:00:00.000Z")).toBe(false);
    expect(isDateTimeValue("0000-06-01T00:00:00.000Z")).toBe(false);
    expect(isDateTimeValue("9999-12-31T23:59:59.999Z")).toBe(true);
    expect(isDateTimeValue("0001-01-01T00:00:00.000Z")).toBe(true);
  });
});

// ── Item 3: what an edit sends ─────────────────────────────────────────────

describe("an edit sends the JSON value the gateway needs (CRM-C4p item 3)", () => {
  it("sends a datetime with Z", () => {
    const sent = toWire(def("datetime"), "2026-10-14T10:30");
    expect(sent).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    expect(checkValue(def("datetime"), sent)).toBeNull();
  });

  it("sends a number as a JSON number, never text", () => {
    expect(JSON.stringify({ v: toWire(def("number"), "1,20,000") })).toBe('{"v":120000}');
    expect(JSON.stringify({ v: toWire(def("decimal"), "12.5") })).toBe('{"v":12.5}');
    expect(typeof toWire(def("currency"), "-3")).toBe("number");
    expect(typeof toWire(def("percent"), ".5")).toBe("number");
  });

  it("keeps a text that is not a number as text, so the check refuses it", () => {
    for (const text of ["12abc", "0x10", "1e", "--1"]) {
      const sent = toWire(def("number"), text);
      expect(typeof sent).toBe("string");
      expect(checkValue(def("number"), sent)).not.toBeNull();
    }
    expect(checkValue(def("datetime"), toWire(def("datetime"), "not a time"))).not.toBeNull();
  });

  it("sends null for a blank text, which clears the value", () => {
    for (const type of ["text", "number", "date", "datetime", "email"]) {
      expect(toWire(def(type), "  ")).toBeNull();
    }
  });

  it("sends each text edit of FieldInput through toWire", () => {
    const source = readFileSync(fileURLToPath(new URL("./FieldInput.tsx", import.meta.url)), "utf8");
    expect(source).toContain("onChange(toWire(def, text))");
    // Every Input and Textarea edit calls `edit`, the one path to `toWire`.
    const edits = source.match(/onChange=\{\(e\) => [a-zA-Z]+\(e\.target\.value\)\}/g) ?? [];
    expect(edits.map((e) => e.includes("edit("))).toEqual([true, true, false]);
    expect(source).toContain("onChange={(e) => setQuery(e.target.value)}");
  });

  it("stores {id, name} for a lookup hit, with the gateway's name of each entity", () => {
    expect(lookupValue("contacts", { id: ID, first_name: "Asha", last_name: "Rao" })).toEqual({ id: ID, name: "Asha Rao" });
    expect(lookupValue("leads", { id: ID, lead_name: "Big lead" })).toEqual({ id: ID, name: "Big lead" });
    expect(lookupValue("organizations", { id: ID, name: "Acme" })).toEqual({ id: ID, name: "Acme" });
    expect(lookupValue("deals", { id: ID, name: "Deal 1" })).toEqual({ id: ID, name: "Deal 1" });
  });
});

// ── Item 4: the empty value and the lock ───────────────────────────────────

describe("the empty value and the Mirror lock (CRM-C4p item 4)", () => {
  it("reads an empty value as —", () => {
    for (const type of FIELD_TYPES) {
      expect(fieldText(formatField(def(type), null))).toBe("—");
      expect(draw(def(type), null, { locked: true })).toContain("—");
    }
    // An empty text input shows — as its placeholder.
    expect(draw(def("text"), null)).toContain('placeholder="—"');
  });

  it("draws no control when locked, and shows LOCK_TIP", () => {
    for (const type of FIELD_TYPES) {
      const html = draw(def(type), PRIMITIVE[type].value, { locked: true });
      expect(html, type).not.toMatch(/<(input|textarea|select|button)\b/);
      expect(html, type).toContain(LOCK_TIP);
    }
  });
});

// ── Item 5: the read of a value ────────────────────────────────────────────

describe("formatField reads an option label and a missing lookup (CRM-C4p item 5)", () => {
  it("shows the option label of a picklist, also for a retired option", () => {
    expect(formatField(def("select"), "gold")).toEqual({ kind: "text", text: "Gold" });
    expect(formatField(def("select"), "bronze")).toEqual({ kind: "text", text: "Bronze" });
    // A value with no option reads as it is.
    expect(formatField(def("select"), "lead")).toEqual({ kind: "text", text: "lead" });
  });

  it("shows the option labels of a multi-select, also for a retired option", () => {
    expect(formatField(def("multiselect"), ["gold", "bronze"])).toEqual({
      kind: "chips",
      items: ["Gold", "Bronze"],
    });
  });

  it("shows the stored name of a missing lookup, and the note", () => {
    const value = { id: ID, name: "Asha Rao", missing: true };
    expect(formatField(def("lookup"), value)).toEqual({ kind: "text", text: `Asha Rao (${LOOKUP_MISSING})` });
    expect(LOOKUP_MISSING).toBe("missing");
    expect(formatField(def("lookup"), { id: ID, name: "Asha Rao" })).toEqual({ kind: "text", text: "Asha Rao" });
    const html = renderToStaticMarkup(createElement(FieldValue, { value: formatField(def("lookup"), value) }));
    expect(html).toContain("Asha Rao (missing)");
  });
});

// ── Items 6 and 7: the read, and the field of a refusal ────────────────────

type Fetch = typeof globalThis.fetch;
const original = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = original;
});

function stub(status: number, body: unknown): string[] {
  const calls: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  }) as Fetch;
  return calls;
}

const LAYOUT = {
  entity: "deals",
  sections: [{ id: "s1", label: "Terms", position: 0 }],
  fields: [
    { key: "name", label: "Deal", type: "text", options: null, position: 0, visible: true, source: "native", api_name: null, is_system: true, required: true },
    { key: "tier", label: "Tier", type: "select", options: OPTIONS, position: 1, visible: true, source: "native", api_name: null, section_id: "s1" },
  ],
  studio: true,
  studio_admin: false,
};

describe("the layout read behind useFieldLayout (CRM-C4p item 6)", () => {
  it("returns studio, studio_admin, the sections and the fields", async () => {
    const calls = stub(200, LAYOUT);
    const layout = await readFieldLayout("deals");
    expect(calls).toEqual(["/api/crm/fields/deals?layout=1"]);
    expect(layout).not.toBeNull();
    expect(layout!.studio).toBe(true);
    expect(layout!.studio_admin).toBe(false);
    expect(layout!.sections).toEqual(LAYOUT.sections);
    expect(layout!.fields.map((f) => f.key)).toEqual(["name", "tier"]);
  });

  it("gives null for a failed read", async () => {
    stub(500, { detail: "boom" });
    expect(await readFieldLayout("deals")).toBeNull();
    stub(404, { detail: "Not Found" });
    expect(await readFieldLayout("leads")).toBeNull();
  });

  it("gives null for a body that is not a layout", async () => {
    stub(200, [{ key: "tier" }]);
    expect(await readFieldLayout("deals")).toBeNull();
  });

  it("reads a missing flag as false", async () => {
    stub(200, { entity: "deals", sections: [], fields: [] });
    const layout = await readFieldLayout("deals");
    expect(layout).toMatchObject({ studio: false, studio_admin: false });
  });
});

describe("a refusal keeps the field it names (CRM-C4p items 4, 5 and 7)", () => {
  it("gives a 422 with an object detail a CrmError with its field", async () => {
    stub(422, { detail: { code: "field_value", message: "Tier: choose one of its options.", field: "tier" } });
    const err = await api.patchRecord("deals", ID, { custom: { tier: "x" } }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CrmError);
    expect(err).toMatchObject({
      status: 422,
      code: "field_value",
      field: "tier",
      message: "Tier: choose one of its options.",
    });
  });

  it("gives a refusal with no field no field", async () => {
    stub(409, { detail: "sync_running" });
    const err = await api.listRecords("deals").catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 409, code: "sync_running", field: undefined });
  });

  it("exports call, the one way to call the gateway", async () => {
    const calls = stub(200, { ok: true });
    expect(await api.call<{ ok: boolean }>("/field-layout/deals", { method: "PUT" })).toEqual({ ok: true });
    expect(calls).toEqual(["/api/crm/field-layout/deals"]);
  });
});
