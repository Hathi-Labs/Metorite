import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MAX_NAV_COUNTS, MAX_NAV_ROWS, MAX_NAV_SECTIONS, NAV_ICONS } from "./manifest";

describe("the caps and the icon list of the rail (STU-AF2a)", () => {
  const file = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.schema.json");
  const schema = JSON.parse(readFileSync(file, "utf-8"));

  it("equal the maxItems of studio_manifest.schema.json", () => {
    expect(schema.properties.nav.maxItems).toBe(MAX_NAV_SECTIONS);
    expect(schema.$defs.NavSection.properties.rows.maxItems).toBe(MAX_NAV_ROWS);
  });

  it("hold the icon list of the schema, in the same order", () => {
    const icons = schema.$defs.NavRow.properties.icon.anyOf[0].enum;
    expect([...NAV_ICONS]).toEqual(icons);
  });
});

describe("the count of a rail row (STU-AF3a)", () => {
  const file = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.py");
  const py = readFileSync(file, "utf-8");

  // The schema has no maxItems for the count of true rows, so the TS cap is read against the Python constant.
  it("holds MAX_NAV_COUNTS equal to the Python constant", () => {
    const m = /^MAX_NAV_COUNTS\s*=\s*(\d+)/m.exec(py);
    expect(m).not.toBeNull();
    expect(MAX_NAV_COUNTS).toBe(Number(m![1]));
  });

  it("gives a row a boolean count and no view, filter or sort", () => {
    const schema = JSON.parse(
      readFileSync(resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.schema.json"), "utf-8"),
    );
    const props = Object.keys(schema.$defs.NavRow.properties);
    expect(props).toContain("count");
    for (const key of ["view", "filter", "sort"]) expect(props).not.toContain(key);
  });
});

