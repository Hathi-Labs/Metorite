/**
 * Every animation in `globals.css` answers a member who asked for less motion.
 *
 * `DESIGN_SYSTEM.md` §4 holds the rule. An `@keyframes` passes when each rule
 * that runs it either sits inside `@media (prefers-reduced-motion:
 * no-preference)`, or names a selector that the `reduce` block stops. The rest
 * must stand in `MOTION_EXEMPT` with the reason the motion stays.
 *
 * ⚠️ This reads `globals.css` only. A `<style>` string inside a component, such
 * as the observability sprites, is outside it. That part is review.
 *
 * Measured 2026-10-14, when this test was written: `.chat-shimmer-text` swept
 * forever under reduced motion, and nothing stopped it.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const GLOBALS = readFileSync(
  fileURLToPath(new URL("../../app/globals.css", import.meta.url)),
  "utf8",
  // A comment goes, and a string stays: `@source "../**/*.tsx"` holds a `/*`.
).replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\/\*[\s\S]*?\*\//g, (_m, str) => str ?? "");

/** Motion that stays under reduced motion, and why. Each entry is a decision. */
const MOTION_EXEMPT: Record<string, string> = {
  // A status loop. The dot says a run is live, and a still dot reads as a hang.
  "chat-pulse-dot": "status",
  // A status loop. The three dots say the agent is working.
  "chat-typing-dot": "status",
  // `swapPanelForm` in `lib/panelMotion.ts` starts no view transition under
  // reduced motion, so these two never run for that member.
  "panel-swap-out": "gated in panelMotion.ts",
  "panel-swap-in": "gated in panelMotion.ts",
};

interface Block {
  prelude: string;
  body: string;
  parents: string[];
}

/** Every `{ … }` block, with the preludes of the blocks around it. */
function blocks(css: string): Block[] {
  const out: Block[] = [];
  const stack: { prelude: string; start: number }[] = [];
  let preludeStart = 0;
  for (let i = 0; i < css.length; i++) {
    const c = css[i];
    if (c === "{") {
      stack.push({ prelude: css.slice(preludeStart, i).trim(), start: i + 1 });
      preludeStart = i + 1;
    } else if (c === "}") {
      const top = stack.pop();
      if (!top) throw new Error(`Unbalanced "}" at ${i} in globals.css`);
      out.push({ prelude: top.prelude, body: css.slice(top.start, i), parents: stack.map((s) => s.prelude) });
      preludeStart = i + 1;
    } else if (c === ";") {
      preludeStart = i + 1;
    }
  }
  if (stack.length) throw new Error("Unclosed block in globals.css");
  return out;
}

const ALL = blocks(GLOBALS);
const isReduce = (p: string) => /prefers-reduced-motion:\s*reduce/.test(p);
const isNoPreference = (p: string) => /prefers-reduced-motion:\s*no-preference/.test(p);
const selectors = (prelude: string) => prelude.split(",").map((s) => s.trim()).filter(Boolean);

const KEYFRAMES = ALL.flatMap((b) => {
  const m = /^@keyframes\s+([\w-]+)$/.exec(b.prelude);
  return m ? [m[1]] : [];
});

/**
 * The selectors the `reduce` block stops. Only a rule that sets the animation
 * to `none` counts. The same selector in a rule that sets a colour stops
 * nothing (`.sidebar-beacon` has both, and the review of 2026-10-14 proved it).
 */
const STOPPED = new Set(
  ALL.filter(
    (b) =>
      !b.prelude.startsWith("@") &&
      b.parents.some(isReduce) &&
      /(?<![\w-])animation(?:-name)?\s*:\s*none\b/.test(b.body),
  ).flatMap((b) => selectors(b.prelude)),
);

/** The rules that run `name`, outside the `reduce` block. */
function runners(name: string): Block[] {
  const runs = new RegExp(`animation(?:-name)?\\s*:[^;]*(?<![\\w-])${name}(?![\\w-])`);
  return ALL.filter(
    (b) => !b.prelude.startsWith("@") && !b.parents.some(isReduce) && runs.test(b.body),
  );
}

function unanswered(name: string): string[] {
  return runners(name)
    // Each selector in the list needs its own answer. One stopped selector
    // does not excuse the others.
    .filter((b) => !b.parents.some(isNoPreference) && !selectors(b.prelude).every((s) => STOPPED.has(s)))
    .map((b) => b.prelude);
}

describe("every animation in globals.css answers reduced motion (DESIGN_SYSTEM.md §4)", () => {
  it("finds the keyframes and the reduce block, so the test reads the real file", () => {
    expect(KEYFRAMES.length).toBeGreaterThan(5);
    expect(STOPPED.size).toBeGreaterThan(3);
  });

  it.each(KEYFRAMES.filter((k) => !(k in MOTION_EXEMPT)))("%s stops for a member who asked for less motion", (name) => {
    expect(runners(name).length, `nothing runs @keyframes ${name}. Delete it.`).toBeGreaterThan(0);
    expect(
      unanswered(name),
      `add the selector to the \`prefers-reduced-motion: reduce\` block, or gate it in \`no-preference\``,
    ).toEqual([]);
  });

  it("each exemption names motion that exists and is still unanswered", () => {
    for (const name of Object.keys(MOTION_EXEMPT)) {
      expect(KEYFRAMES, `MOTION_EXEMPT names ${name}, and globals.css has no such keyframes`).toContain(name);
      expect(
        unanswered(name).length,
        `${name} now stops under reduced motion. Delete its MOTION_EXEMPT entry`,
      ).toBeGreaterThan(0);
    }
  });
});
