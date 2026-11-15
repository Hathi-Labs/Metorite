/**
 * STU-C13 fix round 1: a lines relation in the installed app.
 *
 * A lines relation holds no value: its lines are records of the line object, and
 * the door saves them with the record. So the Form shows no relation-id box for
 * it, and the Table shows no empty column for it.
 *
 * Mutation 1: drop `!isLinesField(f)` from `formFields`. "the Form leaves a lines
 * field out" turns red.
 * Mutation 2: drop the `lines` copy in `fieldSpecs`. Each test turns red.
 * Mutation 3: make `detailFields` return every field. "the Detail leaves a lines
 * field out" turns red. Verifier P2: the Detail showed "Order lines" with a dash
 * for an order that holds lines.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import type { FieldSpec } from "./fieldCheck";
import { detailFields, fieldSpecs, formFields, isLinesField, recordTitle, tableFields, titleKeyOf } from "./packagePreview";
import type { ObjectRow } from "./realDoor";

const field = (key: string, type: string, more: Partial<ObjectRow["fields"][number]> = {}) => ({
  key, type, label: null, required: false, many: false, options: null, to: null, hot: false, ...more,
});

const order: Pick<ObjectRow, "fields"> = {
  fields: [
    field("title", "text"),
    field("lines", "relation", { to: "order_line", lines: "order" }),
    field("customer", "relation", { to: "customer", lines: null }),
    field("total", "number"),
  ],
};

describe("a lines relation in the installed app", () => {
  it("fieldSpecs keeps the lines key of the package read", () => {
    const specs = fieldSpecs(order);
    expect(specs.find((f) => f.key === "lines")?.lines).toBe("order");
    expect(specs.find((f) => f.key === "customer")?.lines).toBeUndefined();
    expect(specs.filter(isLinesField).map((f) => f.key)).toEqual(["lines"]);
  });

  it("the Form leaves a lines field out, and keeps a plain relation", () => {
    const keys = formFields(fieldSpecs(order), new Set(["total"])).map((f) => f.key);
    expect(keys).toEqual(["title", "customer"]);
  });

  it("the Detail leaves a lines field out, and keeps a plain relation and a computed field", () => {
    expect(detailFields(fieldSpecs(order)).map((f) => f.key)).toEqual(["title", "customer", "total"]);
  });

  it("the Table leaves a lines field out, and keeps a plain relation", () => {
    expect(tableFields(fieldSpecs(order)).map((f) => f.key)).toEqual(["title", "customer", "total"]);
  });
});

describe("REL-R1: the title rule, shared with the gateway", () => {
  // The gateway reads the same table (tests/unit/test_studio_relation_names.py). It cuts a
  // title at 160 characters, and the client does not, so no case holds a longer one.
  const table = JSON.parse(
    readFileSync(new URL("../../../../../tests/fixtures/studio_title_rule.json", import.meta.url), "utf8"),
  ) as { cases: { name: string; fields: FieldSpec[]; data: Record<string, unknown>; key: string | null; title: string | null }[] };
  for (const c of table.cases) {
    it(c.name, () => {
      expect(titleKeyOf(c.fields)).toBe(c.key);
      expect(recordTitle(c.fields, { id: "the-id", ...c.data })).toBe(c.title ?? "the-id");
    });
  }
});
