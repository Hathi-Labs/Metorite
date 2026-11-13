/**
 * The TypeScript error codes and limits equal the golden file (STU-L6a).
 * tests/unit/test_studio_code_error_codes.py pins the same file to Python.
 */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { STUDIO_CODE_ERRORS } from "./codeErrors";
import { STUDIO_LIMITS } from "./limits";

const ROOT = resolve(__dirname, "../../../../..");
const GOLDEN = join(ROOT, "tests/unit/golden/studio_code_errors.json");
const golden = JSON.parse(readFileSync(GOLDEN, "utf-8")) as {
  codes: Record<string, string>;
  limits: Record<string, Record<string, number>>;
};

describe("studio code errors", () => {
  it("holds the same codes and sentences as the golden file", () => {
    expect(STUDIO_CODE_ERRORS).toEqual(golden.codes);
  });
  it("holds the same limits as the golden file", () => {
    expect(STUDIO_LIMITS).toEqual(golden.limits);
  });
  it("holds eleven codes", () => {
    expect(Object.keys(STUDIO_CODE_ERRORS)).toHaveLength(11);
  });
});
