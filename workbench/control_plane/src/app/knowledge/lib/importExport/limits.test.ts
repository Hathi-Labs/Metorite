import { describe, expect, it } from "vitest";

import {
  ImportRefusal,
  MAX_DEPTH,
  MAX_ENTRIES,
  MAX_FILE_BYTES,
  MAX_PAGES,
  MAX_TOTAL_BYTES,
  checkDeclared,
  safeEntryPath,
} from "./limits";

describe("the limits of an import", () => {
  it("are the numbers that the spec names", () => {
    expect(MAX_FILE_BYTES).toBe(25 * 1024 * 1024);
    expect(MAX_TOTAL_BYTES).toBe(100 * 1024 * 1024);
    expect(MAX_ENTRIES).toBe(500);
    expect(MAX_PAGES).toBe(300);
    expect(MAX_DEPTH).toBe(12);
  });
});

describe("safeEntryPath", () => {
  it("keeps a plain path and drops ./ parts", () => {
    expect(safeEntryPath("a/b/c.md")).toBe("a/b/c.md");
    expect(safeEntryPath("./a//b.md")).toBe("a/b.md");
    expect(safeEntryPath("a\\b.md")).toBe("a/b.md");
    expect(safeEntryPath("folder/")).toBe("folder/");
  });
  it("refuses a .. part", () => {
    expect(() => safeEntryPath("../x.md")).toThrow(ImportRefusal);
    expect(() => safeEntryPath("a/../../x.md")).toThrow(/leaves its folder/);
    expect(() => safeEntryPath("a\\..\\x.md")).toThrow(ImportRefusal);
  });
  it("refuses an absolute path and a drive letter", () => {
    expect(() => safeEntryPath("/etc/passwd.md")).toThrow(/absolute/);
    expect(() => safeEntryPath("C:/x.md")).toThrow(/absolute/);
    expect(() => safeEntryPath("\\\\share\\x.md")).toThrow(/absolute/);
  });
  it("refuses a NUL", () => {
    expect(() => safeEntryPath("a\0.md")).toThrow(/NUL/);
  });
});

describe("checkDeclared", () => {
  it("passes a small zip", () => {
    expect(() => checkDeclared([{ name: "a.md", size: 10 }])).not.toThrow();
  });
  it("refuses more than 500 entries", () => {
    const many = Array.from({ length: MAX_ENTRIES + 1 }, (_, i) => ({ name: `f${i}.md`, size: 1 }));
    expect(() => checkDeclared(many)).toThrow(/501 entries/);
    expect(() => checkDeclared(many.slice(0, MAX_ENTRIES))).not.toThrow();
  });
  it("refuses an entry that declares more than 25 MB", () => {
    expect(() => checkDeclared([{ name: "bomb.md", size: MAX_FILE_BYTES + 1 }])).toThrow(/limit for one file/);
    expect(() => checkDeclared([{ name: "ok.md", size: MAX_FILE_BYTES }])).not.toThrow();
  });
  it("refuses more than 100 MB in total", () => {
    const four = Array.from({ length: 5 }, (_, i) => ({ name: `f${i}.md`, size: 24 * 1024 * 1024 }));
    expect(() => checkDeclared(four)).toThrow(/limit for one import/);
    expect(() => checkDeclared([{ name: "a.md", size: 1 }], MAX_TOTAL_BYTES)).toThrow(/limit for one import/);
  });
  it("refuses a bad path in the list", () => {
    expect(() => checkDeclared([{ name: "../x.md", size: 1 }])).toThrow(ImportRefusal);
  });
});
