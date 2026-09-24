/**
 * The Projects priority vocabulary, and where it meets My Tasks.
 *
 * Owner decision, 2026-09-23. One task sits in both apps (D53), and the two
 * spoke about priority in words that collided:
 *
 *   • Projects labelled `importance = 3` **"Urgent"** — a manual ranking.
 *   • My Tasks has an `urgent` DERIVED from the due date, never set by hand.
 *
 * So "Urgent" in one app and "urgent" in the other disagreed about the same
 * task, and both apps were behaving as designed. Level 3 is "Highest" now:
 * this priority says how much the task matters to the company, and the due
 * date says when.
 *
 * These read SOURCE as well as values, because two of the places that spelt
 * the labels were a `.tsx` and a private constant, which no value test can
 * reach (D-PM-21).
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ORG_PRIORITY_SEED } from "@/app/tasks/lib/priority";

import { IMPORTANCE_OPTIONS, importanceLabel } from "./table";

const PROJECTS = fileURLToPath(new URL("..", import.meta.url));

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.ts$/.test(name)) out.push(path);
  }
  return out;
}

/** Code only. A comment explaining the rule is allowed to name the old word. */
const code = (path: string) =>
  readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

describe("the Projects priority scale", () => {
  it("calls level 3 Highest", () => {
    expect(importanceLabel(3)).toBe("Highest");
  });

  it("is Highest, High, Normal, Low — four distinct words plus the unset row", () => {
    expect(IMPORTANCE_OPTIONS.map((o) => o.label)).toEqual([
      "No priority",
      "Highest",
      "High",
      "Normal",
      "Low",
    ]);
  });

  it("never says 'Urgent' anywhere in the Projects app", () => {
    // 🔴 The word belongs to My Tasks, where it is derived from the due date.
    // A Projects surface that says "Urgent" re-creates the collision this
    // decision removed. Comments may name it; code may not.
    const offenders = sources(PROJECTS).filter((f) => /["'`]Urgent["'`]/.test(code(f)));
    expect(offenders).toEqual([]);
  });

  it("is spelt in ONE place, not three", () => {
    // The bulk bar and the group-by lanes each carried their own copy until
    // 2026-09-23. A second copy is how one surface says "Highest" while
    // another still says the old word.
    const copies = sources(PROJECTS).filter((f) =>
      /["'`]High["'`][\s\S]{0,80}["'`]Normal["'`][\s\S]{0,80}["'`]Low["'`]/.test(code(f)),
    );
    expect(copies.map((f) => f.replace(/\\/g, "/").split("/projects/")[1])).toEqual([
      "lib/table.ts",
    ]);
  });
});

describe("where the two apps meet", () => {
  it("seeds My Tasks' Important from a level that EXISTS on this scale", () => {
    // R7: `ORG_PRIORITY_SEED` lives in the Tasks app and reads a number from
    // this one. If this scale is renumbered, the seed would silently point at
    // a different word — or at none.
    expect(importanceLabel(ORG_PRIORITY_SEED)).toBe("High");
  });

  it("seeds from High upward, so Highest seeds too", () => {
    const seeding = IMPORTANCE_OPTIONS.filter(
      (o) => o.value !== "" && Number(o.value) >= ORG_PRIORITY_SEED,
    ).map((o) => o.label);
    expect(seeding).toEqual(["Highest", "High"]);
  });
});
