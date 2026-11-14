import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MAX_NAV_ROWS, MAX_NAV_SECTIONS, NAV_ICONS } from "./manifest";

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
