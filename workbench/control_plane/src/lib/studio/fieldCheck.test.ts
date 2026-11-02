/**
 * STU-D2a fence: the client field check equals the Python one.
 *
 * `studio_field_cases.json` is written by
 * `tests/unit/test_studio_field_cases_parity.py` from `studio_fields.py`, the
 * one source. Every case here comes from that file. The Python test holds the
 * limits of `fieldCheck.ts` equal to the Python constants.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  type FieldSpec, checkValue, isBlank, validateRecord,
} from "./fieldCheck";

const ROOT = resolve(__dirname, "../../../../..");
const GOLDEN = join(ROOT, "packages/acb_common/acb_common/studio_field_cases.json");

interface ValueCase {
  id: string;
  field: FieldSpec;
  value: unknown;
  ok: boolean;
  out?: unknown;
  out_same?: boolean;
}
interface RecordCase {
  id: string;
  fields: FieldSpec[];
  data: unknown;
  ok: boolean;
  out?: Record<string, unknown>;
  error_field?: string;
  record_error?: boolean;
}

/** Expand a `{"$repeat": "a", "count": 2000}` marker, at any depth. */
function expand(x: unknown): unknown {
  if (Array.isArray(x)) return x.map(expand);
  if (x && typeof x === "object") {
    const o = x as Record<string, unknown>;
    if (typeof o.$repeat === "string") {
      return String(o.prefix ?? "") + o.$repeat.repeat(Number(o.count));
    }
    return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, expand(v)]));
  }
  return x;
}

const golden = JSON.parse(readFileSync(GOLDEN, "utf-8")) as {
  values: ValueCase[];
  records: RecordCase[];
};

describe("the golden file", () => {
  it("holds at least 48 value cases and every field type three times", () => {
    expect(golden.values.length).toBeGreaterThanOrEqual(48);
    const types = new Set(golden.values.map((c) => c.field.type));
    expect(types.size).toBe(16);
    for (const t of types) {
      const mine = golden.values.filter((c) => c.field.type === t && !c.field.many);
      expect(mine.length, t).toBeGreaterThanOrEqual(3);
      expect(mine.some((c) => c.ok), `${t} valid`).toBe(true);
      expect(mine.some((c) => !c.ok), `${t} invalid`).toBe(true);
    }
  });
});

describe("checkValue equals check_value", () => {
  for (const c of golden.values) {
    it(c.id, () => {
      const value = expand(c.value);
      const r = checkValue(c.field, value);
      expect(r.ok, `accept or reject of ${c.id}`).toBe(c.ok);
      if (r.ok) expect(r.value).toEqual(c.out_same ? value : c.out);
      else expect(r.message.length).toBeGreaterThan(0);
    });
  }
});

describe("validateRecord equals validate_record", () => {
  for (const c of golden.records) {
    it(c.id, () => {
      const r = validateRecord(c.fields, expand(c.data));
      expect(r.ok, `accept or reject of ${c.id}`).toBe(c.ok);
      if (r.ok) expect(r.value).toEqual(c.out);
      else if (c.record_error) expect(r.notObject).toBe(true);
      else expect(r.problems[0].field).toBe(c.error_field);
    });
  }
});

describe("what the form needs beyond the golden file", () => {
  const fields: FieldSpec[] = [
    { key: "a", type: "text", required: true },
    { key: "b", type: "number", required: true },
  ];
  it("reports every bad field, in field order", () => {
    const r = validateRecord(fields, { a: "", b: "x" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.problems.map((p) => p.field)).toEqual(["a", "b"]);
  });
  it("reads a field named like an object method as missing", () => {
    const r = validateRecord([{ key: "constructor", type: "text", required: true }], {});
    expect(r.ok).toBe(false);
  });
  it("calls blank what Python strip() calls blank, and no more", () => {
    expect(["\x85", "\x1f", "\x1c", "\u2028", " \t"].every(isBlank)).toBe(true);
    expect(isBlank("\ufeff")).toBe(false);
  });
  it("calls a blank value blank", () => {
    expect([null, undefined, "", "  ", []].every(isBlank)).toBe(true);
    expect([0, false, "a", [0]].some(isBlank)).toBe(false);
  });
  it("never echoes the value in a message", () => {
    const r = checkValue({ key: "f", type: "email" }, "secret-value");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.message).not.toContain("secret-value");
  });
});

describe("no createContext outside a client file (the next build trap)", () => {
  // `chatPlacement.ts`, a server route, reaches `lib/studio`. A server module
  // may not import `createContext`, and the build fails when it does.
  const dir = resolve(__dirname);
  const files = readdirSync(dir).filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith(".test.ts"));
  it("scans the folder", () => expect(files.length).toBeGreaterThan(4));
  for (const f of files) {
    it(f, () => {
      const src = readFileSync(join(dir, f), "utf-8");
      if (/\bcreateContext\b/.test(src.replace(/\/\*[\s\S]*?\*\//g, ""))) {
        expect(src.trimStart().startsWith('"use client"'), `${f} makes a context`).toBe(true);
      }
    });
  }
  it("fieldCheck.ts imports no React", () => {
    expect(readFileSync(join(dir, "fieldCheck.ts"), "utf-8")).not.toMatch(/from ["']react/);
  });
});
