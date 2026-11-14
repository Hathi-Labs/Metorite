import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { connector, connectorRows } from "./connectors";

const FILE = join(
  resolve(__dirname, "../../../.."),
  "packages/acb_common/acb_common/connector_list.json",
);

describe("connector list", () => {
  it("holds the rows of the one JSON file", () => {
    const doc = JSON.parse(readFileSync(FILE, "utf-8"));
    expect(connectorRows()).toEqual(doc.rows);
  });
  it("finds a row by id", () => {
    expect(connector("zoho")?.owning_app).toBe("crm");
    expect(connector("nope")).toBeUndefined();
  });
});
