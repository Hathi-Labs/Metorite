// Field filters on the module lists (WS-53 CRM-U9, crm_platform.md §20.12).
// The runner has no DOM, so every rule here is pure.
import { describe, expect, it } from "vitest";

import type { FieldDef } from "../lib/fields";
import { customFilters, setCustom, setRange } from "../lib/filterBar";
import { activeFilterChips, filterFields, groupFields, removeFilter } from "../lib/filterFields";
import { moduleListQuery } from "../lib/modules";
import { DEFAULT_VIEW, parseView, serializeView } from "../lib/urlState";

const def = (key: string, type: string, fill: number | null = null): FieldDef => ({
  key,
  label: key.replace("_", " "),
  type,
  options:
    type === "select"
      ? [
          { label: "Paid", value: "Paid" },
          { label: "Open", value: "Open" },
        ]
      : null,
  position: 0,
  visible: true,
  source: "zoho",
  api_name: key,
  fill,
});

const defs = [def("Status", "select", 0.9), def("Total", "number", 0.5), def("Paid", "boolean"), def("Note", "text")];
const moduleView = { ...DEFAULT_VIEW, tab: "module" as const, module: "Invoices", layout: "list" as const };

describe("the + Filter list of a module", () => {
  const fields = filterFields(null, customFilters(defs, true), true);

  it("offers Updated and the filterable fields of the module, and no Source", () => {
    const labels = fields.map((f) => f.label);
    expect(labels).toEqual(expect.arrayContaining(["Updated", "Status", "Total", "Paid"]));
    expect(labels).not.toContain("Source");
    expect(labels).not.toContain("Created");
    expect(labels).not.toContain("Closing");
    expect(labels).not.toContain("Note");
  });

  it("groups them as the core lists do", () => {
    expect(groupFields(fields).map((g) => g.group)).toEqual(["Dates", "Picklists", "Numbers", "Other"]);
  });

  it("still offers Updated when the definitions did not load", () => {
    expect(filterFields(null, customFilters(null, true), true).map((f) => f.label)).toEqual(["Updated"]);
  });
});

describe("the chips of a module list", () => {
  const fields = filterFields(null, customFilters(defs, true), true);

  it("names a picklist choice and an updated range, and removes each", () => {
    let v = setCustom(moduleView, "Status", "Paid");
    v = setRange(setRange(v, "updated_from", "2026-10-01"), "updated_to", "2026-10-31");
    const chips = activeFilterChips(v, fields, 2026);
    expect(chips.map((c) => c.label)).toEqual(["Updated: 1 Oct–31 Oct", "Status: Paid"]);
    expect(removeFilter(v, "custom:Status").custom).toEqual({});
    expect(removeFilter(v, "range:updated").ranges).toEqual({});
  });
});

describe("the request of a module list", () => {
  it("sends owner, the updated range and custom.<key>.in", () => {
    const query = moduleListQuery({
      ...moduleView,
      owner: "me",
      ranges: { updated_from: "2026-10-01", created_from: "2026-01-01", close_to: "2026-12-01" },
      custom: { Status: ["Paid", "Open"], Empty: [] },
    });
    expect(query).toMatchObject({
      owner: "me",
      updated_from: "2026-10-01",
      "custom.Status.in": "Paid,Open",
    });
    expect(query).not.toHaveProperty("created_from");
    expect(query).not.toHaveProperty("close_to");
    expect(query).not.toHaveProperty("custom.Empty.in");
  });
});

describe("the URL of a module tab", () => {
  it("round trips cf.<key>, owner and the updated range", () => {
    const view = {
      ...moduleView,
      owner: "me",
      ranges: { updated_from: "2026-10-01" },
      custom: { Status: ["Paid"] },
    };
    const qs = serializeView(view);
    expect(qs).toContain("module=Invoices");
    expect(qs).toContain("cf.Status=Paid");
    expect(parseView(qs)).toMatchObject({
      tab: "module",
      module: "Invoices",
      owner: "me",
      ranges: { updated_from: "2026-10-01" },
      custom: { Status: ["Paid"] },
    });
  });
});
