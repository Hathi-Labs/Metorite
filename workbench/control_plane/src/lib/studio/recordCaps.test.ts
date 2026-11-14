import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MAX_RECORD_TABS } from "./manifest";
import { RECORD_TAB_IDS } from "./recordPageModel";

describe("the caps of the record page (STU-AF5b)", () => {
  const file = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.schema.json");
  const schema = JSON.parse(readFileSync(file, "utf-8"));

  it("equal the maxItems of studio_manifest.schema.json", () => {
    expect(schema.$defs.RecordDef.properties.tabs.maxItems).toBe(MAX_RECORD_TABS);
  });

  it("hold the tab ids of the schema, in the same order", () => {
    expect([...RECORD_TAB_IDS]).toEqual(schema.$defs.RecordDef.properties.tabs.items.enum);
  });
});
