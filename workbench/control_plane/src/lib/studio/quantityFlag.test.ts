/**
 * STU-C24a fence: the flag of a quantity shows in the Table cell, the Detail sheet and the
 * Form, and a Table cell never edits a quantity inline. The twin of the Python check is
 * held by `fieldCheck.test.ts` through the golden file.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/blocks-preview",
}));

import StudioDetail from "@/components/studio/StudioDetail";
import { FormView } from "@/components/studio/StudioForm";
import { FLAG_WORDS, displayValue, fieldGrid } from "./blocks";
import type { FieldSpec } from "./fieldCheck";
import { INLINE_TYPES, isInlineEditable } from "./inlineEdit";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const GLU: FieldSpec = {
  key: "glucose", type: "quantity", label: "Glucose", units: ["mmol/L", "mg/dL", "mEq"],
  normal: { low: "3.9", high: "5.6" }, critical: { low: "2.8", high: "10" },
  conversions: [{ from: "mg/dL", to: "mmol/L", factor: "0.0555" }],
};
const q = (value: string, unit = "mmol/L") => ({ value, unit });

describe("the words of the flag", () => {
  it("adds the word after the value, and nothing for normal or none", () => {
    expect(displayValue(GLU, q("6"))).toBe("6 mmol/L (High)");
    expect(displayValue(GLU, q("3"))).toBe("3 mmol/L (Low)");
    expect(displayValue(GLU, q("2"))).toBe("2 mmol/L (Critical low)");
    expect(displayValue(GLU, q("11"))).toBe("11 mmol/L (Critical high)");
    expect(displayValue(GLU, q("5", "mEq"))).toBe("5 mEq (Not checked)");
    expect(displayValue(GLU, q("5"))).toBe("5 mmol/L");
    expect(displayValue({ ...GLU, normal: undefined, critical: undefined }, q("99"))).toBe("99 mmol/L");
  });
  it("never uses the word Safe", () => {
    expect(Object.values(FLAG_WORDS).join(" ")).not.toMatch(/safe/i);
  });
  it("the Table cell and the Detail sheet say the same words", () => {
    const row = { glucose: q("11") };
    const cell = fieldGrid([GLU], [row]).rows[0].cells[0];
    expect(cell).toBe("11 mmol/L (Critical high)");
    const html = renderToStaticMarkup(createElement(StudioDetail, { fields: [GLU], record: row }));
    expect(html).toContain("11 mmol/L");
    expect(html).toContain("Critical high");
    expect(html).toContain('data-flag="critical_high"');
  });
  it("the Detail chip comes from statusAccent: warning for high, danger for critical", () => {
    const warn = renderToStaticMarkup(createElement(StudioDetail, { fields: [GLU], record: { glucose: q("6") } }));
    expect(warn).toContain("bg-warning/10 text-warning");
    const bad = renderToStaticMarkup(createElement(StudioDetail, { fields: [GLU], record: { glucose: q("11") } }));
    expect(bad).toContain("bg-destructive/10 text-destructive");
    const ok = renderToStaticMarkup(createElement(StudioDetail, { fields: [GLU], record: { glucose: q("5") } }));
    expect(ok).not.toContain("data-flag");
  });
});

describe("the Form shows the live flag under the input", () => {
  const form = (draft: Record<string, string>) =>
    renderToStaticMarkup(createElement(FormView, {
      fields: [GLU], drafts: { glucose: draft }, errors: {}, formId: "f1",
      onDraft: () => {}, onBlur: () => {}, onSubmit: () => {},
    }));
  it("shows the word for a flagged draft", () => {
    const html = form({ value: "11", unit: "mmol/L" });
    expect(html).toContain("Flag: Critical high");
    expect(html).toContain("text-destructive");
  });
  it("shows nothing for a normal, an empty or a half-typed draft", () => {
    expect(form({ value: "5", unit: "mmol/L" })).not.toContain("Flag:");
    expect(form({ value: "", unit: "mmol/L" })).not.toContain("Flag:");
    expect(form({ value: "11.", unit: "mmol/L" })).not.toContain("Flag:");
  });
  it("keeps one live region for the flag, empty when no flag shows", () => {
    const empty = form({ value: "5", unit: "mmol/L" });
    expect(empty).toContain('role="status"');
    expect(empty).toContain('aria-live="polite"');
    expect(form({ value: "11", unit: "mmol/L" })).toContain('aria-live="polite"');
  });
});

describe("a Table cell never edits a quantity", () => {
  it("a quantity is not an inline type", () => {
    expect(INLINE_TYPES.has("quantity")).toBe(false);
    expect(isInlineEditable(GLU, new Set())).toBe(false);
  });
});

describe("the flag arithmetic is exact", () => {
  it("quantityFlag uses no float: no Number(), parseFloat or Math. in its body", () => {
    const src = readFileSync(resolve(__dirname, "fieldCheck.ts"), "utf-8");
    const start = src.indexOf("export function quantityFlag(");
    const end = src.indexOf("\nfunction percent(", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    const body = src.slice(start, end);
    expect(body).not.toMatch(/\bNumber\(|parseFloat|\bMath\./);
    // The helper that reads a decimal is exact too.
    const helper = src.slice(src.indexOf("Decimal text as a BigInt scaled"), start);
    expect(helper).not.toMatch(/\bNumber\(|parseFloat|\bMath\./);
  });
});
