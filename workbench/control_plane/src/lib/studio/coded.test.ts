/**
 * The coded field type (WS-STU C23a). A value is {system, code}. The golden file
 * holds the value cases, and the codes in it are made up. The check reads no list.
 * Mutation: drop the `v.system !== f.system` line in `fieldCheck.ts`, and the
 * "refuses another system" case turns red.
 */
import { describe, expect, it } from "vitest";
import { FIELD_TYPE_LIST, displayValue, draftToValue, emptyDraft, parseFields, valueToDraft } from "./blocks";
import { MAX_CODED_CHARS, checkValue, type FieldSpec } from "./fieldCheck";
import { isInlineEditable } from "./inlineEdit";
import { TYPE_LABELS } from "./draftEdit";

const f: FieldSpec = { key: "dx", type: "coded", system: "X-SYS" };
const ok = (code: string) => ({ system: "X-SYS", code });

describe("the coded type", () => {
  it("is the 24th type of the one list, with a label", () => {
    expect(FIELD_TYPE_LIST).toHaveLength(24);
    expect(FIELD_TYPE_LIST[23]).toBe("coded");
    expect(TYPE_LABELS.coded).toBe("Coded value");
  });
  it("takes a value of the system of the field", () => {
    expect(checkValue(f, ok("X-0001"))).toEqual({ ok: true, value: ok("X-0001") });
    expect(checkValue(f, ok("c".repeat(MAX_CODED_CHARS))).ok).toBe(true);
  });
  it("refuses another system", () => {
    expect(checkValue(f, { system: "OTHER", code: "X-0001" }).ok).toBe(false);
  });
  it("refuses an empty, long, spaced or control code, and an extra key", () => {
    for (const bad of [
      ok(""), ok("c".repeat(MAX_CODED_CHARS + 1)), ok(" X-1"), ok("X-1 "), ok("X-1 "), ok("X\t1"),
      { ...ok("X-1"), display: "A" }, { system: "X-SYS" }, "X-1", null,
    ]) expect(checkValue(f, bad).ok).toBe(false);
  });
  it("keeps its system when a field is read", () => {
    expect(parseFields([{ key: "dx", type: "coded", system: "X-SYS" }])[0]).toMatchObject({ system: "X-SYS" });
  });
});

describe("the form, the table and the sheet", () => {
  it("the form holds the code, and the value gets the system of the field", () => {
    expect(emptyDraft(f)).toBe("");
    expect(draftToValue(f, " X-0001 ")).toEqual(ok("X-0001"));
    expect(draftToValue(f, "  ")).toBeUndefined();
    expect(valueToDraft(f, ok("X-0001"))).toBe("X-0001");
  });
  it("a cell and the sheet show the code only", () => {
    expect(displayValue(f, ok("X-0001"))).toBe("X-0001");
    expect(displayValue({ ...f, many: true }, [ok("X-1"), ok("X-2")])).toBe("X-1, X-2");
  });
  it("no cell edits it", () => {
    expect(isInlineEditable(f, new Set())).toBe(false);
  });
});
