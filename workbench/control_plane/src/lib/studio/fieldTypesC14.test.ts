/**
 * STU-C14 fence: the six more field types in the Form, the Detail and the Table.
 *
 * The value check is held to Python by `fieldCheck.test.ts` (the golden file).
 * This file holds the rest: a form input for each type, the draft that a member
 * types and the value it stands for, and the words of each stored value.
 *
 * Mutation 1: in `parseDuration`, read "1h 30m" as 90 minutes of hours (drop `* 60`
 * of the minutes). "reads hours, minutes and seconds" turns red.
 * Mutation 2: in `studio_fields._barcode`, swap the EAN-13 weights (`1 if i % 2 else 3`).
 * `test_each_type_takes_a_good_value[barcode]` and `test_the_barcode_formats` turn red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/blocks-preview",
}));

import GenerativeUINode from "@/components/GenerativeUINode";
import StudioDetail from "@/components/studio/StudioDetail";
import { FormView, controlId, errorIdOf } from "@/components/studio/StudioForm";
import { clearAll, put } from "@/lib/dataCache";
import {
  displayValue, draftToValue, emptyDraft, formatDuration, formatPercent, parseDuration, parsePercent,
  parseFields, plainMarkdown, tableBlock, valueToDraft,
} from "./blocks";
import { StudioDataContext } from "./dataContext";
import { type Row, type StudioDataDoor, sourceKey } from "./dataDoor";
import { type FieldSpec, checkValue, ean13Valid } from "./fieldCheck";
import { checkForm, initialDrafts } from "./formModel";
import { TYPE_LABELS } from "./draftEdit";
import { fieldSpecs } from "./packagePreview";
import type { ObjectRow } from "./realDoor";

beforeEach(() => clearAll());

const FIELDS: FieldSpec[] = [
  { key: "report", type: "richText", label: "Report" },
  { key: "score", type: "rating", label: "Score", min: 1, max: 5 },
  { key: "spent", type: "duration", label: "Time spent" },
  { key: "load", type: "quantity", label: "Load", units: ["kg", "t"] },
  { key: "discount", type: "percent", label: "Discount" },
  { key: "serial", type: "barcode", label: "Serial", format: "ean13" },
];

const noop = () => {};
const form = (errors: Record<string, string> = {}, drafts = initialDrafts(FIELDS)) =>
  renderToStaticMarkup(createElement(FormView, {
    fields: FIELDS, drafts, errors, formId: "f1", onDraft: noop, onBlur: noop, onSubmit: noop,
  }));
const tagOf = (html: string, id: string): string =>
  html.match(new RegExp("<[^>]*id=\"" + id + "\"[^>]*>"))?.[0] ?? "";

describe("Form: an input for each new type", () => {
  const html = form();
  it("draws a house control with the id of its field", () => {
    for (const f of FIELDS) {
      expect(html).toContain("data-type=\"" + f.type + "\"");
      expect(tagOf(html, controlId("f1", f.key)), f.key).toContain("cc-control");
    }
  });
  it("draws a rich text as a text area, and a quantity as a value and a unit", () => {
    expect(tagOf(html, controlId("f1", "report"))).toMatch(/^<textarea/);
    expect(html).toContain("aria-label=\"Load value\"");
    expect(html).toContain("aria-label=\"Load unit\"");
  });
  it("states the rule of a rating and a barcode in a hint", () => {
    expect(html).toContain("A whole number from 1 to 5");
    expect(html).toContain("13 digits, the last one a check digit");
  });
  it("sets aria-invalid and aria-describedby on a bad control", () => {
    const bad = form({ score: "Enter a whole number from 1 to 5.", load: "Choose one of the listed units." });
    for (const key of ["score", "load"]) {
      const tag = tagOf(bad, controlId("f1", key));
      expect(tag, key).toContain("aria-invalid=\"true\"");
      expect(tag, key).toContain("aria-describedby=\"" + errorIdOf("f1", key) + "\"");
    }
  });
  it("uses no raw colour", () => {
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(/);
  });
});

describe("the draft and the value", () => {
  const field = (key: string) => FIELDS.find((f) => f.key === key) as FieldSpec;
  it("reads hours, minutes and seconds", () => {
    expect(parseDuration("1h 30m")).toBe(5400);
    expect(parseDuration("45m")).toBe(2700);
    expect(parseDuration("90s")).toBe(90);
    expect(parseDuration("1h30m15s")).toBe(5415);
    expect(parseDuration("1:30")).toBe(5400);
    expect(parseDuration("1:30:15")).toBe(5415);
    expect(parseDuration("120")).toBe(120);
    expect(parseDuration("soon")).toBe("soon");
    expect(parseDuration("1:75")).toBe("1:75");
  });
  it("words a duration", () => {
    expect([0, 59, 60, 3600, 5400, 5415].map(formatDuration)).toEqual(["0s", "59s", "1m", "1h", "1h 30m", "1h 30m 15s"]);
  });
  it("turns percent text into basis points, with no float", () => {
    expect(parsePercent("12.5")).toBe(1250);
    expect(parsePercent("12.5%")).toBe(1250);
    expect(parsePercent("0.29")).toBe(29);
    expect(parsePercent("-5")).toBe(-500);
    expect(parsePercent("1.005")).toBe("1.005");
    expect(parsePercent("abc")).toBe("abc");
    expect([1250, 29, -500, 0, -5].map(formatPercent)).toEqual(["12.5", "0.29", "-5", "0", "-0.05"]);
  });
  it("round-trips a stored value through its draft and the check", () => {
    const stored: Record<string, unknown> = {
      report: "A **bold** line", score: 4, spent: 5400, load: { value: "1250.5", unit: "kg" },
      discount: 1250, serial: "4006381333931",
    };
    for (const f of FIELDS) {
      const draft = valueToDraft(f, stored[f.key]);
      const back = draftToValue(f, draft);
      expect(back, f.key).toEqual(stored[f.key]);
      expect(checkValue(f, back).ok, f.key).toBe(true);
    }
  });
  it("leaves a blank control out of the record", () => {
    for (const f of FIELDS) expect(draftToValue(f, emptyDraft(f)), f.key).toBeUndefined();
  });
  it("starts a quantity on its first unit", () => {
    expect(emptyDraft(field("load"))).toEqual({ value: "", unit: "kg" });
  });
  it("keeps text that fits no shape, so the check refuses it", () => {
    expect(checkValue(field("score"), draftToValue(field("score"), "9")).ok).toBe(false);
    expect(checkValue(field("score"), draftToValue(field("score"), "x")).ok).toBe(false);
    expect(checkValue(field("spent"), draftToValue(field("spent"), "soon")).ok).toBe(false);
    expect(checkValue(field("discount"), draftToValue(field("discount"), "1.005")).ok).toBe(false);
    expect(checkValue(field("load"), draftToValue(field("load"), { value: "1e3", unit: "kg" })).ok).toBe(false);
  });
  it("blocks a submit on a bad barcode and names the field", () => {
    const drafts = initialDrafts(FIELDS);
    drafts.serial = "4006381333932";
    const out = checkForm(FIELDS, drafts);
    expect(out.ok).toBe(false);
    if (!out.ok) expect(out.first).toBe("serial");
  });
  it("checks the EAN-13 check digit", () => {
    expect(ean13Valid("4006381333931")).toBe(true);
    expect(ean13Valid("4006381333932")).toBe(false);
    expect(ean13Valid("5901234123457")).toBe(true);
  });
  it("reads the new parts of a field from a model-written node", () => {
    const [f] = parseFields([{ key: "k", type: "rating", min: 0, max: 10, units: ["x", 5], format: "qr" }]);
    expect(f).toMatchObject({ min: 0, max: 10, units: ["x"], format: "qr" });
  });
});

describe("the words of a stored value", () => {
  it("reads each type the same in every block", () => {
    expect(displayValue(FIELDS[1], 4)).toBe("4 / 5");
    expect(displayValue({ key: "s", type: "rating" }, 3)).toBe("3 / 5");
    expect(displayValue(FIELDS[2], 5400)).toBe("1h 30m");
    expect(displayValue(FIELDS[3], { value: "1250.5", unit: "kg" })).toBe("1250.5 kg");
    expect(displayValue(FIELDS[4], 1250)).toBe("12.5%");
    expect(displayValue(FIELDS[5], "4006381333931")).toBe("4006381333931");
  });
  it("strips markdown to plain words for a cell", () => {
    expect(plainMarkdown("## Title\n\nA **bold** [link](https://example.com) and `code`.")).toBe(
      "Title A bold link and code.");
  });
  it("shows nothing for a value of the wrong shape", () => {
    expect(displayValue(FIELDS[2], "5400")).toBe("");
    expect(displayValue(FIELDS[3], { value: 5 })).toBe("");
  });
});

describe("Detail and Table draw the new types", () => {
  const row: Row = {
    id: "r1", report: "The **base plate** is level.", score: 4, spent: 5400,
    load: { value: "1250.5", unit: "kg" }, discount: 1250, serial: "4006381333931",
  };
  const detail = renderToStaticMarkup(createElement(StudioDetail, { fields: FIELDS, record: row }));
  const door: StudioDataDoor = { scope: "c14:draft", read: async () => [], run: async () => ({ ok: true }) };
  const source = { action: "object:visit.list" };
  clearAll();
  put<Row[]>(sourceKey(door, source), [row]);
  const table = renderToStaticMarkup(createElement(StudioDataContext.Provider, { value: door },
    createElement(GenerativeUINode, { spec: tableBlock(FIELDS, source, "Visits"), onAction: noop })));

  it("shows the words in the Detail", () => {
    for (const w of ["4 / 5", "1h 30m", "1250.5 kg", "12.5%", "4006381333931"]) expect(detail, w).toContain(w);
    expect(detail).toMatch(/<strong[^>]*>base plate<\/strong>/);
  });
  it("draws a barcode in the mono face", () => {
    expect(detail).toMatch(/font-mono[^>]*>4006381333931/);
  });
  it("shows a dash for a missing value", () => {
    const sparse = renderToStaticMarkup(createElement(StudioDetail, { fields: FIELDS, record: { id: "x" } }));
    expect(sparse.match(/—/g)?.length).toBe(FIELDS.length);
  });
  it("shows the same words in the Table, with the rich text plain", () => {
    for (const w of ["4 / 5", "1h 30m", "1250.5 kg", "12.5%", "4006381333931", "The base plate is level."]) {
      expect(table, w).toContain(w);
    }
    expect(table).not.toContain("**");
  });
});

describe("the installed-app path (fix round)", () => {
  const row: ObjectRow = {
    id: "o1", name: "visit", label: "Visit", rules: [],
    fields: [
      { key: "load", type: "quantity", label: "Load", required: true, many: false, options: null, to: null, hot: false,
        units: ["kg", "t"] },
      { key: "score", type: "rating", label: "Score", required: false, many: false, options: null, to: null, hot: false,
        min: 0, max: 10 },
      { key: "serial", type: "barcode", label: "Serial", required: false, many: false, options: null, to: null, hot: false,
        format: "ean13" },
    ],
  };
  it("fieldSpecs keeps the units, the bounds and the format of the gateway row", () => {
    const [load, score, serial] = fieldSpecs(row);
    expect(load.units).toEqual(["kg", "t"]);
    expect([score.min, score.max]).toEqual([0, 10]);
    expect(serial.format).toBe("ean13");
  });
  it("a required quantity saves, and a rating of 7 passes, on those fields", () => {
    const fields = fieldSpecs(row);
    const drafts = initialDrafts(fields);
    drafts.load = { value: "12.5", unit: "kg" };
    drafts.score = "7";
    expect(checkForm(fields, drafts).ok).toBe(true);
    const html = renderToStaticMarkup(createElement(FormView, {
      fields, drafts: initialDrafts(fields), errors: {}, formId: "p1", onDraft: noop, onBlur: noop, onSubmit: noop,
    }));
    expect(html).toContain("13 digits, the last one a check digit");
    expect(html).toContain("A whole number from 0 to 10");
  });
});

describe("messages in the units of the input (fix round)", () => {
  it("a percent message speaks percent, and a duration message speaks hours and minutes", () => {
    const percent = checkValue({ key: "d", type: "percent" }, 1.5);
    const duration = checkValue({ key: "t", type: "duration" }, -1);
    expect(percent.ok || percent.message).toBe("Enter a percent with up to 2 decimals, as 12.5.");
    expect(duration.ok || duration.message).toContain("1h 30m");
    expect(percent.ok || percent.message).not.toContain("basis");
  });
});

describe("messages that name the cause (fix round 2)", () => {
  const msg = (f: FieldSpec, v: unknown) => {
    const r = checkValue(f, v);
    return r.ok ? "" : r.message;
  };
  it("a < before a letter reads as a tag, and the message says how to fix it", () => {
    expect(msg({ key: "r", type: "richText" }, "load<limit")).toBe(
      "A < before a letter reads as a tag. Put a space after it.");
  });
  it("a value over the maximum names the maximum", () => {
    expect(msg({ key: "t", type: "duration" }, 3_000_000 * 3600)).toContain("2,777,777 hours");
    expect(msg({ key: "p", type: "percent" }, 99_999_999_999)).toContain("9,999,999.99");
    expect(msg({ key: "t", type: "duration" }, -1)).toContain("1h 30m");
  });
});

describe("the Data tab and the Detail (fix round)", () => {
  it("labels each new type", () => {
    for (const t of ["richText", "rating", "duration", "quantity", "percent", "barcode"]) {
      expect(TYPE_LABELS[t], t).toBeTruthy();
      expect(TYPE_LABELS[t]).not.toBe(t);
    }
  });
  it("draws a rich text of many values one by one", () => {
    const html = renderToStaticMarkup(createElement(StudioDetail, {
      fields: [{ key: "r", type: "richText", many: true }], record: { r: ["**a**", "**b**"] },
    }));
    expect(html).toMatch(/<strong[^>]*>a<\/strong>/);
    expect(html).toMatch(/<strong[^>]*>b<\/strong>/);
  });
  it("caps a heading in a rich text at the size of the sheet", () => {
    const html = renderToStaticMarkup(createElement(StudioDetail, {
      fields: [{ key: "r", type: "richText" }], record: { r: "## Findings" },
    }));
    expect(html).toContain("[&amp;_h2]:!text-xs");
  });
});

describe("the first heading of a rich text (fix round 3)", () => {
  it("has no top margin, so the label sits on its first line", () => {
    const html = renderToStaticMarkup(createElement(StudioDetail, {
      fields: [{ key: "r", type: "richText" }], record: { r: "## Findings" },
    }));
    expect(html).toContain("[&amp;_h2:first-child]:!mt-0");
  });
});
