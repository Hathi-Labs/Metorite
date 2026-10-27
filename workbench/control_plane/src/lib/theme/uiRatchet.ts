/**
 * The source scan and the ratchet behind the UI gate (`DESIGN_SYSTEM.md` §11).
 *
 * Owner, 2026-10-27: count today's violations, keep them, and block a new
 * one. So each rule that a regex can see holds a budget for each file:
 *
 * - a file with no budget must be clean,
 * - a file with a budget may not go over it,
 * - a file that went under its budget fails until its number comes down.
 *
 * The third check keeps the budgets true. A budget that only goes down when
 * somebody happens to notice becomes fiction.
 *
 * The rules live in the test files (`theme/motion.test.ts` and
 * `uiGates.test.ts`), never here. This file is source, so a rule string in it
 * would count against it.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, it } from "vitest";

/** The `src/` folder. Every path below is relative to it, with `/`. */
export const SRC = fileURLToPath(new URL("../..", import.meta.url));

/** Every `.ts`, `.tsx` and `.css` file under `src/`, without the test files. */
export function uiSources(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(tsx?|css)$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(SRC);
  return out.sort();
}

/**
 * A string, or a comment. A string comes first, so `accept="image/*"` and
 * `@source "../**"` do not open a comment. A `//` after a `:` is a URL.
 */
const STRING_OR_COMMENT =
  /("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|\/\*[\s\S]*?\*\/|(?<![:"'/\\\w])\/\/[^\n]*/g;

/** A source file with its comments blanked. A string stays, and each line keeps its number. */
export function readSource(rel: string): string {
  return readFileSync(join(SRC, rel), "utf8").replace(
    STRING_OR_COMMENT,
    (match, str: string | undefined) => str ?? match.replace(/[^\n]/g, ""),
  );
}

/** The number of matches of a global regex. */
export function countMatches(text: string, re: RegExp): number {
  if (!re.global) throw new Error(`countMatches needs a global regex: ${re}`);
  return text.match(re)?.length ?? 0;
}

export interface Ratchet {
  /** The gate id and the rule, as the failure message says them. */
  rule: string;
  /** What to write instead. */
  fix: string;
  /** The count of each file that the rule reads. A file at 0 may be left out. */
  measure: () => Record<string, number>;
  /** The budget of each file, measured when the rule was written. */
  budget: Record<string, number>;
}

/** Register the three checks of one ratchet in the current `describe`. */
export function holdRatchet({ rule, fix, measure, budget }: Ratchet): void {
  let cached: Record<string, number> | undefined;
  const counts = () => (cached ??= measure());
  const drift = () =>
    Object.entries(budget).map(([file, allowed]) => ({ file, budget: allowed, actual: counts()[file] ?? 0 }));

  it(`${rule}: a file with no budget is clean`, () => {
    const fresh = Object.entries(counts()).filter(([file, n]) => n > 0 && !(file in budget));
    expect(fresh, `${rule}. ${fix} (DESIGN_SYSTEM.md §11)`).toEqual([]);
  });

  it(`${rule}: no file goes over its budget`, () => {
    const worse = drift().filter((row) => row.actual > row.budget);
    expect(worse, `${rule}. ${fix} Do not raise a budget. (DESIGN_SYSTEM.md §11)`).toEqual([]);
  });

  it(`${rule}: no budget is stale`, () => {
    const better = drift().filter((row) => row.actual < row.budget);
    expect(
      better,
      `${rule}: these files got better. Set each budget to its actual count, and delete an entry at 0.`,
    ).toEqual([]);
  });
}
