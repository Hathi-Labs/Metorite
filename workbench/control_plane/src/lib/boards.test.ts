/**
 * Each Kanban board draws its lanes with `BoardColumn` (WS-53 CRM-C2,
 * `crm_platform.md` §20.5).
 *
 * The owner said that the CRM board looked different from the Projects board,
 * and that the colour cap of a column fought its round corner. One primitive
 * now draws a lane, in `src/components/board/`. This fence keeps each board on
 * it. A board that goes back to its own column brings back its own width, its
 * own radius and its own stripe, and nothing else in CI would notice.
 *
 * ⚠️ This is a SOURCE fence. It proves that a board renders the primitive and
 * draws no column of its own. It does not prove how a lane looks. The
 * `visual-review` skill is that gate.
 *
 * A new board joins `BOARDS` in the PR that builds it. That step is review,
 * and no test holds it.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));

/** Each board of the product, and what its lanes are. */
const BOARDS: Record<string, string> = {
  "app/projects/components/TaskBoard.tsx": "the Projects columns and the swimlane cells",
  "app/tasks/components/TaskBoard.tsx": "the three My Tasks stage columns",
  "app/crm/components/KanbanBoard.tsx": "the Deals and Leads stage lanes",
};

/** The shared primitive. The stripe and the column-shape rules read it too. */
const BOARD_DIR = "components/board";

const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/** Source with its comments removed, so a note about an old class is not code. */
const code = (rel: string) =>
  read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(?<![:"'/])\/\/[^\n]*/g, "");

/**
 * Every class list in a file, each one as a set of tokens.
 *
 * Two shapes count. The first is each `className=` value, read to its closing
 * brace, with every string in it joined into one set. So an array or a ternary
 * that splits the classes over three strings is still one list. The second is
 * each string literal by itself, so a class string held in a constant counts
 * too. A set has no order, so `flex-col shrink-0 w-72` and `w-72 flex-col
 * shrink-0` are the same list.
 */
function classLists(source: string): Set<string>[] {
  const lists: Set<string>[] = [];
  const literals = (text: string): string[] =>
    [...text.matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)].map(
      (m) => (m[1] ?? m[2] ?? m[3] ?? "").replace(/\$\{[^}]*\}/g, " "),
    );
  const tokens = (parts: string[]) =>
    new Set(parts.join(" ").split(/\s+/).filter(Boolean));

  for (const m of source.matchAll(/className=/g)) {
    let i = m.index + m[0].length;
    if (source[i] === '"' || source[i] === "'") {
      const end = source.indexOf(source[i], i + 1);
      lists.push(tokens([source.slice(i + 1, end)]));
      continue;
    }
    if (source[i] !== "{") continue;
    let depth = 0;
    const start = i;
    for (; i < source.length; i++) {
      if (source[i] === "{") depth++;
      else if (source[i] === "}" && --depth === 0) break;
    }
    lists.push(tokens(literals(source.slice(start, i + 1))));
  }
  for (const literal of literals(source)) lists.push(tokens([literal]));
  return lists;
}

/** A token with any variant prefix taken off: `md:w-72` is `w-72`. */
const bare = (token: string) => token.replace(/^(?:[\w-]+:)*!?/, "");

/** `w-<n>` or `w-[...]`. `w-full`, `w-max` and `min-w-0` are not a width. */
const isWidth = (token: string) => /^w-(?:\d+(?:\.\d+)?|\[[^\]]+\])$/.test(bare(token));

/** The three tokens of a hand-drawn column: a fixed width that does not shrink, stacked. */
function drawsOwnColumn(list: Set<string>): boolean {
  const all = [...list].map(bare);
  return all.includes("shrink-0") && all.includes("flex-col") && [...list].some(isWidth);
}

/** A full-width bar a few pixels high: the cap the round corner clips. */
function drawsStripe(list: Set<string>): boolean {
  const all = [...list].map(bare);
  return all.includes("w-full") && all.some((t) => /^h-(?:0\.5|1|1\.5)$/.test(t));
}

/** The component files that one board file imports, as paths under `src/`. */
function importedComponents(rel: string): string[] {
  const out: string[] = [];
  for (const m of code(rel).matchAll(/from\s+["']([^"']+)["']/g)) {
    const spec = m[1];
    let base: string | null = null;
    if (spec.startsWith("@/")) base = spec.slice(2);
    else if (spec.startsWith(".")) base = join(dirname(rel), spec).split("\\").join("/");
    if (!base) continue;
    for (const ext of [".tsx", ".ts"]) {
      try {
        read(base + ext);
        out.push(base + ext);
        break;
      } catch {
        /* not this extension */
      }
    }
  }
  return out;
}

const boardDirFiles = () =>
  readdirSync(join(SRC, BOARD_DIR))
    .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
    .map((f) => `${BOARD_DIR}/${f}`);

describe("the class-list reader", () => {
  // Guards the shape of every assertion below. A reader that finds nothing
  // passes every `filter(...).toEqual([])` in this file.
  it("reads a column in any class order, in a string, a template or an array", () => {
    for (const source of [
      `<div className="flex w-72 shrink-0 flex-col" />`,
      `<div className="flex-col shrink-0 w-[264px]" />`,
      "<div className={`shrink-0 ${x} flex-col w-80`} />",
      `<div className={["relative w-72", on ? "a" : "b", "flex-col shrink-0"].join(" ")} />`,
      `const COLUMN = "md:w-72 flex flex-col shrink-0";`,
    ]) {
      expect(classLists(source).some(drawsOwnColumn), source).toBe(true);
    }
  });

  it("does not call a full-width or shrinking box a column", () => {
    for (const source of [
      `<div className="flex w-full shrink-0 flex-col" />`,
      `<div className="flex w-72 shrink-0 items-center" />`,
      `<div className="flex min-w-0 flex-1 flex-col" />`,
    ]) {
      expect(classLists(source).some(drawsOwnColumn), source).toBe(false);
    }
  });

  it("reads a stripe in any class order", () => {
    expect(classLists("<div className={`h-1 w-full ${accent.dot}`} />").some(drawsStripe)).toBe(true);
    expect(classLists(`<div className="w-full bg-info h-0.5" />`).some(drawsStripe)).toBe(true);
    expect(classLists(`<div className="h-1.5 rounded bg-primary/50" />`).some(drawsStripe)).toBe(false);
  });
});

describe("each board draws its lanes with BoardColumn", () => {
  for (const [file, lanes] of Object.entries(BOARDS)) {
    it(`${file} — ${lanes}`, () => {
      const source = code(file);
      expect(source).toMatch(
        /import\s*\{[^}]*\bBoardColumn\b[^}]*\}\s*from\s*["']@\/components\/board\/BoardColumn["']/,
      );
      expect(source).toContain("<BoardColumn");
    });
  }

  it("no board draws a column of its own, in any class order", () => {
    const offenders = Object.keys(BOARDS).filter((f) =>
      classLists(code(f)).some(drawsOwnColumn),
    );
    expect(
      offenders,
      "A board holds `shrink-0`, `flex-col` and a width in one class list. " +
        "That is a hand-drawn lane. Render `BoardColumn` instead.",
    ).toEqual([]);
  });

  it("each board renders its cards through TaskCardShell, at one remove at most", () => {
    // Tasks reaches the shell through `TaskCard.tsx`, so one level of
    // indirection is allowed. Two levels would hide a card of its own.
    const offenders = Object.keys(BOARDS).filter((f) => {
      if (code(f).includes("<TaskCardShell")) return false;
      return !importedComponents(f).some((dep) => code(dep).includes("<TaskCardShell"));
    });
    expect(
      offenders,
      "A board draws a card that is not `TaskCardShell`. Extend the shell " +
        "with the prop the card needs.",
    ).toEqual([]);
  });

  it("no lane wears a stripe", () => {
    // The owner's complaint: a `h-1 w-full` cap under `overflow-hidden
    // rounded-lg` meets the corner curve, and the clip leaves a sliver at
    // each end. The scope is the boards and the primitive only, so a
    // progress bar elsewhere stays legal.
    const files = [...Object.keys(BOARDS), ...boardDirFiles()];
    const offenders = files.filter((f) => classLists(code(f)).some(drawsStripe));
    expect(
      offenders,
      "A full-width bar a few pixels high. The stage colour lives in the " +
        "inset chip (`StageChip`), which no corner can clip.",
    ).toEqual([]);
  });

  /**
   * Moved here from `sharedTaskUi.test.ts` (CRM-C2), over three boards now.
   *
   * ⚠️ NOT a theming rule. `globals.css` maps `--radius-xl` to `--radius`, so
   * `rounded-xl` IS themed. It is a continuity rule: three boards drew one
   * object at two corner radii, and that alone made them read as two
   * products.
   */
  it("the boards draw their columns with the same radius", () => {
    const offenders = Object.keys(BOARDS).filter((f) =>
      /\brounded-(?:xl|2xl|3xl)\b/.test(code(f)),
    );
    expect(
      offenders,
      "The boards must draw one shape. `BoardColumn` owns the corner " +
        "(`rounded-lg`), and its chip nests inside it (`rounded-md`).",
    ).toEqual([]);
  });
});
