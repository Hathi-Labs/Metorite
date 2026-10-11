/**
 * One rule for how a person reads (`personName.ts`). Owner, 2026-10-10:
 * names, never email IDs, with the address in the hover.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { rowLabel } from "@/app/projects/lib/capacity";
import { personLabel } from "@/app/projects/lib/grouping";

import { emailLocalPart, isEmailLike, personDisplayName, realName } from "./personName";

describe("the rule", () => {
  it("keeps a real name", () => {
    expect(realName("  Sougata Maji ")).toBe("Sougata Maji");
    expect(personDisplayName("Sougata Maji", "sougata@fracktal.in")).toBe("Sougata Maji");
  });

  it("treats an address in the name field as no name — the production case", () => {
    // 9 of 38 directory rows on 2026-10-10 held the address as the name.
    expect(realName("vjvarada@fracktal.in")).toBeNull();
    expect(personDisplayName("vjvarada@fracktal.in", "vjvarada@fracktal.in")).toBe("vjvarada");
  });

  it("falls back to the left half of the address, never the whole address", () => {
    expect(personDisplayName(null, "priya@acme.in")).toBe("priya");
    expect(personDisplayName("   ", "priya@acme.in")).toBe("priya");
    expect(emailLocalPart("noat")).toBe("noat");
  });

  it("knows an address when it sees one", () => {
    expect(isEmailLike("a@b.co")).toBe(true);
    expect(isEmailLike("Ana Lima")).toBe(false);
    expect(isEmailLike("@handle")).toBe(false);
  });
});

describe("the surfaces read it", () => {
  it("Capacity's row label never prints the address", () => {
    const row = {
      assignee: "vjvarada@fracktal.in",
      name: "vjvarada@fracktal.in",
      kind: "person",
    } as Parameters<typeof rowLabel>[0];
    expect(rowLabel(row)).toBe("vjvarada");
  });

  it("the board's label ignores an address in the directory", () => {
    const names = new Map([["vjvarada@fracktal.in", "vjvarada@fracktal.in"]]);
    expect(personLabel("vjvarada@fracktal.in", names)).toBe("vjvarada");
    expect(personLabel("agent:builder", names)).toBe("builder");
  });

  it("no person surface grows a `name || email` fallback back (sweep)", () => {
    // The pattern that printed the address while looking like a name. Each
    // file below printed a person through it until 2026-10-10.
    // Every source file of the Projects app, not a list somebody keeps: the
    // review of #863 found five more sites that a fixed list of four missed.
    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((entry) => {
        const path = join(dir, entry);
        if (statSync(path).isDirectory()) return walk(path);
        return /\.(ts|tsx)$/.test(entry) && !/\.test\.tsx?$/.test(entry) ? [path] : [];
      });
    const projects = fileURLToPath(new URL("../app/projects", import.meta.url));
    const files = [...walk(projects), fileURLToPath(new URL("./reportEmail.ts", import.meta.url))];
    const fallback = /\.name\s*\|\|\s*[\w?.]*(?:assignee|email|created_by)\b/;
    const offenders = files.filter((file) => fallback.test(readFileSync(file, "utf-8")));
    expect(offenders, "these print an address as a name").toEqual([]);
  });
});
