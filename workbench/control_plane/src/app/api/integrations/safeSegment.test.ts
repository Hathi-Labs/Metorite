import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { unsafeSegment } from "./safeSegment";

describe("unsafeSegment", () => {
  it.each(["..", "%2e%2e", ".", "a/b", "a%2fb", "", "a..b", "a b", "a?b"])("refuses %s", (s) => {
    expect(unsafeSegment(s)).toBe(true);
  });
  it.each(["mcp:brave-search", "brave_search", "a.b", "dana@x.test", "123e4567-e89b"])("allows %s", (s) => {
    expect(unsafeSegment(s)).toBe(false);
  });
});

describe("the dynamic routes call the guard before any fetch", () => {
  it.each([
    "connections/[id]/disconnect/route.ts",
    "mcp/[name]/test/route.ts",
    "mcp/[name]/route.ts",
    "plugins/[id]/route.ts",
  ])("%s", (rel) => {
    const src = readFileSync(join(__dirname, rel), { encoding: "utf-8" });
    const guard = src.indexOf("refuseBadSegment(");
    expect(guard, "no guard").toBeGreaterThan(-1);
    expect(guard).toBeLessThan(src.indexOf("gatewayFetch("));
  });
});
