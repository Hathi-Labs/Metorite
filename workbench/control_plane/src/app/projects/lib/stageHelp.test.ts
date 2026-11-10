/**
 * WS-41 I-10b build rule 4 — the meanings of "status" and "stage" have ONE
 * home, `src/lib/statusCategory.ts` (`CATEGORY_HINT`, `STATUS_AND_STAGE`).
 *
 * Every help text under `src/app/projects/` reads them. A copy goes stale
 * on the day somebody edits the meaning, and then two screens disagree about
 * what a stage is. This fails when a file holds, as a literal, any of those
 * texts. A paraphrase cannot be fenced, so that half of the rule is advisory
 * (R7).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { CATEGORY_HINT, STATUS_AND_STAGE } from "@/lib/statusCategory";

const ROOT = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sourceFiles(path));
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) out.push(path);
  }
  return out;
}

/** A source text as one line, so a string split over `+` lines still reads whole. */
function flatten(src: string): string {
  return src.replace(/["'`]\s*\+\s*["'`]/g, "").replace(/\s+/g, " ");
}

describe("the meanings of status and stage live in one place", () => {
  const texts = [STATUS_AND_STAGE, ...Object.values(CATEGORY_HINT)];
  const files = sourceFiles(ROOT);

  it("reads files under src/app/projects", () => {
    expect(files.length).toBeGreaterThan(20);
    expect(files.some((f) => f.endsWith("ImportDialog.tsx"))).toBe(true);
  });

  it("no file there keeps a copy of a meaning", () => {
    const copies: string[] = [];
    for (const file of files) {
      const flat = flatten(readFileSync(file, "utf-8"));
      for (const text of texts) {
        if (flat.includes(text.replace(/\s+/g, " "))) copies.push(`${file}: ${text.slice(0, 40)}…`);
      }
    }
    expect(copies).toEqual([]);
  });

  it("the help screens read the shared texts", () => {
    const help = readFileSync(join(ROOT, "components", "StageHelp.tsx"), "utf-8");
    expect(help).toContain("STATUS_AND_STAGE");
    expect(help).toContain("CATEGORY_HINT");
  });

  it("the In progress meaning names waiting, blocked and review work (I-10b)", () => {
    expect(CATEGORY_HINT.in_progress).toMatch(/waits/);
    expect(CATEGORY_HINT.in_progress).toMatch(/blocked/);
    expect(CATEGORY_HINT.in_progress).toMatch(/review/);
  });
});
