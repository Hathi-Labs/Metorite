/**
 * The autonumber field type (WS-STU C15a). The store fills it, so no form,
 * sheet or cell sends its key. The golden file holds the value cases.
 */
import { describe, expect, it } from "vitest";
import { FIELD_TYPE_LIST, draftToValue, parseFields } from "./blocks";
import { autonumberText, checkValue, type FieldSpec } from "./fieldCheck";
import { computedKeys, isInlineEditable } from "./inlineEdit";
import { formFields } from "./packagePreview";

const no: FieldSpec = { key: "no", type: "autonumber", prefix: "FV-", pad: 4 };
const title: FieldSpec = { key: "title", type: "text" };

describe("the autonumber type", () => {
  it("is the 23rd type of the one list", () => {
    expect(FIELD_TYPE_LIST).toHaveLength(23);
    expect(FIELD_TYPE_LIST[22]).toBe("autonumber");
  });
  it("checks the stored form: a prefix, then digits", () => {
    for (const good of ["FV-0042", "0001", "INV/000002"]) expect(checkValue(no, good)).toEqual({ ok: true, value: good });
    for (const bad of ["", "FV-", "FV 0042", 42, null]) expect(checkValue(no, bad).ok).toBe(false);
  });
  it("words a number as the store does", () => {
    expect(autonumberText(no, 7)).toBe("FV-0007");
    expect(autonumberText(no, 12345)).toBe("FV-12345");
    expect(autonumberText({ key: "n", type: "autonumber" }, 3)).toBe("0003");
    expect(autonumberText({ key: "n", type: "autonumber", prefix: "N", pad: 1 }, 10)).toBe("N10");
  });
  it("keeps its parts when a field is read", () => {
    const [f] = parseFields([{ key: "no", type: "autonumber", prefix: "FV-", pad: 6, reset: "day" }]);
    expect(f).toMatchObject({ prefix: "FV-", pad: 6, reset: "day" });
  });
});

describe("no form, sheet or cell sends the key", () => {
  it("the form fields leave it out", () => {
    expect(formFields([no, title], new Set())).toEqual([title]);
  });
  it("it joins the computed keys, and no cell edits it", () => {
    expect(computedKeys([], [no, title])).toEqual(new Set(["no"]));
    expect(computedKeys([{ kind: "formula", target: "t" }], [no]).has("t")).toBe(true);
    expect(isInlineEditable(no, new Set())).toBe(false);
  });
  it("a draft of it makes no value", () => {
    expect(draftToValue(no, "FV-0001")).toBeUndefined();
  });
});
