// CRM-C6a: the lead-conversion mapping, Native mode (spec crm_platform.md
// section 20.9). The runner has no DOM, so the view is rendered to a string,
// the rules are called as functions, and a wiring rule is a scan of the source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AUTO,
  MAP_ENTITIES,
  NO_LEAD_FIELDS,
  SKIP,
  choiceOf,
  choiceOptions,
  convertSkippedNote,
  offersSetToAuto,
  problemText,
  rowsWith,
  tableRows,
  type ConversionMapping,
  type MappingCell,
} from "../lib/convertMapping";
import { ConvertMappingView } from "./ConvertMappingCard";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

const cell = (over: Partial<MappingCell> = {}): MappingCell => ({
  target_key: null,
  reason: "auto",
  problem: null,
  choices: [],
  ...over,
});

/** Two lead fields: "region" (auto to a same-key field) and "budget". */
const SERVED: ConversionMapping = {
  fields: [
    {
      key: "region",
      label: "Region",
      type: "text",
      mapping: {
        contact: cell({ target_key: "region", choices: ["region", "notes"] }),
        company: cell({ reason: "skip" }),
        deal: cell(),
      },
    },
    {
      key: "budget",
      label: "Budget",
      type: "number",
      mapping: {
        contact: cell(),
        company: cell(),
        deal: cell({ target_key: "amount_x", reason: "explicit", choices: ["amount_x"] }),
      },
    },
  ],
  targets: {
    contact: [
      { key: "region", label: "Region", type: "text", retired: false },
      { key: "notes", label: "Notes", type: "textarea", retired: false },
    ],
    company: [],
    deal: [
      { key: "amount_x", label: "Amount X", type: "decimal", retired: false },
      { key: "old", label: "Old", type: "text", retired: true },
    ],
  },
};

describe("a cell and its choices", () => {
  it("shows Auto, Skip or the explicit field", () => {
    expect(choiceOf(cell())).toBe(AUTO);
    expect(choiceOf(cell({ target_key: "region" }))).toBe(AUTO);
    expect(choiceOf(cell({ reason: "skip" }))).toBe(SKIP);
    expect(choiceOf(cell({ reason: "explicit", target_key: "amount_x" }))).toBe("amount_x");
  });

  it("offers Auto, Skip and each compatible field, with labels", () => {
    const options = choiceOptions(SERVED.fields[0].mapping.contact, SERVED.targets.contact);
    expect(options.map((o) => o.value)).toEqual([AUTO, SKIP, "region", "notes"]);
    expect(options.map((o) => o.label)).toEqual(["Auto: Region", "Skip", "Region", "Notes"]);
    const none = choiceOptions(cell(), []);
    expect(none[0]).toEqual({ value: AUTO, label: "Auto", hint: "no match" });
  });

  it("keeps a retired explicit target in the list, disabled, so the button names it", () => {
    const retired = cell({ reason: "explicit", target_key: "old", problem: "target_retired" });
    const options = choiceOptions(retired, SERVED.targets.deal);
    expect(options.at(-1)).toEqual({ value: "old", label: "Old", hint: "retired", disabled: true });
  });
});

describe("the whole map for a PUT", () => {
  it("changes one cell and keeps each other choice", () => {
    expect(rowsWith(SERVED, { source: "region", entity: "deal", choice: SKIP })).toEqual([
      { source_key: "region", target_entity: "company", skip: true },
      { source_key: "region", target_entity: "deal", skip: true },
      { source_key: "budget", target_entity: "deal", target_key: "amount_x" },
    ]);
  });

  it("sends no row for an auto cell, because no row means auto", () => {
    expect(rowsWith(SERVED, { source: "budget", entity: "deal", choice: AUTO })).toEqual([
      { source_key: "region", target_entity: "company", skip: true },
    ]);
    expect(rowsWith(SERVED, { source: "region", entity: "contact", choice: "notes" })).toContainEqual({
      source_key: "region",
      target_entity: "contact",
      target_key: "notes",
    });
  });
});

describe("the tables", () => {
  const html = (mapping: ConversionMapping, error: string | null = null) =>
    renderToStaticMarkup(
      createElement(ConvertMappingView, { mapping, saving: false, error, onChange: () => undefined }),
    );

  it("draws one table for each target entity, with one row for each lead field", () => {
    const out = html(SERVED);
    expect(out).toContain(">Field mapping</h2>");
    for (const { label } of MAP_ENTITIES) expect(out).toContain(`aria-label="Lead to ${label}"`);
    expect(MAP_ENTITIES.map((e) => e.entity)).toEqual(["contact", "company", "deal"]);
    expect(tableRows(SERVED, "deal").map((r) => r.field.key)).toEqual(["region", "budget"]);
    // Each row is a SelectButton: a button that names the field and the entity.
    expect(out).toContain('aria-label="Budget to Deal');
    expect(out).toContain("Auto: Region");
    expect(out).toContain("Amount X");
  });

  it("says what to do when no custom lead field exists", () => {
    expect(html({ fields: [], targets: SERVED.targets })).toContain(NO_LEAD_FIELDS);
  });

  it("shows a refused write and the problem of a target", () => {
    const broken: ConversionMapping = {
      ...SERVED,
      fields: [
        {
          ...SERVED.fields[1],
          mapping: {
            ...SERVED.fields[1].mapping,
            deal: cell({ reason: "explicit", target_key: "old", problem: "target_retired" }),
          },
        },
      ],
    };
    const out = html(broken, "A refusal of the gateway.");
    expect(out).toContain('role="alert"');
    expect(out).toContain("Old is retired. Set this cell to Auto or Skip, or restore the field.");
    // Review P1: one click puts the broken cell back to Auto.
    expect(out).toContain(">Set to Auto<");
    expect(html(SERVED)).not.toContain("Set to Auto");
  });
});

describe("a broken cell (review round 1, P1)", () => {
  const retired = cell({ reason: "explicit", target_key: "old", problem: "target_retired" });

  it("names the target and the way out", () => {
    expect(problemText(retired, SERVED.targets.deal)).toBe(
      "Old is retired. Set this cell to Auto or Skip, or restore the field.",
    );
    expect(problemText(cell({ reason: "explicit", target_key: "x", problem: "type_mismatch" }), [])).toBe(
      "x has a type that cannot take this value now. Set this cell to Auto or Skip.",
    );
    expect(problemText(cell(), [])).toBeNull();
    expect(problemText(cell({ problem: "type_mismatch" }), [])).toMatch(/Auto copies nothing here/);
  });

  it("offers Set to Auto on an explicit broken cell only", () => {
    expect(offersSetToAuto(retired)).toBe(true);
    expect(offersSetToAuto(cell({ problem: "type_mismatch" }))).toBe(false);
    expect(offersSetToAuto(cell({ reason: "explicit", target_key: "amount_x" }))).toBe(false);
  });

  it("keeps the broken row in a save of another cell, because the gateway accepts it unchanged", () => {
    const broken: ConversionMapping = {
      ...SERVED,
      fields: [{ ...SERVED.fields[1], mapping: { ...SERVED.fields[1].mapping, deal: retired } }],
    };
    expect(rowsWith(broken, { source: "budget", entity: "contact", choice: SKIP })).toEqual([
      { source_key: "budget", target_entity: "contact", skip: true },
      { source_key: "budget", target_entity: "deal", target_key: "old" },
    ]);
    expect(rowsWith(broken, { source: "budget", entity: "deal", choice: AUTO })).toEqual([]);
  });
});

describe("the note after a conversion", () => {
  it("counts and names each value that was not copied", () => {
    expect(convertSkippedNote(undefined)).toBeNull();
    expect(convertSkippedNote([])).toBeNull();
    expect(
      convertSkippedNote([
        { entity: "deal", source_key: "grade", target_key: "grade", reason: "option_inactive", message: "" },
      ]),
    ).toBe("1 field was not copied: grade to deal (no active option).");
    expect(
      convertSkippedNote([
        { entity: "deal", source_key: "a", target_key: "a", reason: "type_mismatch", message: "" },
        { entity: "contact", source_key: "b", target_key: "b", reason: "record_has_value", message: "" },
      ]),
    ).toBe("2 fields were not copied: a to deal (wrong type), b to contact (the record kept its value).");
  });

  it("is a toast of the page after the convert", () => {
    expect(read("../page.tsx")).toMatch(/convertSkippedNote\(result\?\.skipped\)/);
  });
});

describe("the card source", () => {
  const card = read("./ConvertMappingCard.tsx");
  const lib = read("../lib/convertMapping.ts");

  it("uses the shared primitives only", () => {
    expect(card).toMatch(/from "@\/components\/ui\/SelectButton"/);
    expect(card).not.toMatch(/<(select|input|button)\b/);
    expect(card).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });

  it("calls the gateway through `call` and navigates through no second path", () => {
    expect(lib).toMatch(/import \{ call \} from "\.\/api"/);
    expect(lib + card).not.toMatch(/\bfetch\(/);
    expect(card).not.toMatch(/next\/link|next\/navigation|router\.push|location\.href/);
  });
});
