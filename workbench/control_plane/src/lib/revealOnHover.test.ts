/**
 * A control revealed by hover must be revealed on a TOUCH-CAPABLE display too.
 *
 * 🔴 **`opacity-0 group-hover:opacity-100` hides the control forever there,
 * and thirty places in this app were written that way.**
 *
 * Tailwind v4 compiles every `hover:` and `group-hover:` inside
 * `@media (hover: hover)`. Chromium reports `hover: none` for ANY display the
 * OS calls touch-capable — a Windows laptop with a touchscreen, driven by a
 * mouse, with a cursor on the screen. The reveal rule is then never emitted
 * and the element stays at `opacity: 0` for the life of the page.
 *
 * The owner reported it on the Projects board on 2026-09-20. H-131 counted
 * nineteen more files: the `/tasks` inbox bulk-select, the calendar's resize
 * handle, the email sidebar actions, the app-builder row actions.
 *
 * ⚠️ **The obvious fix is also wrong, and this gate refuses it too.** Pinning
 * with `[@media(hover:none)]:opacity-100` makes the control permanently
 * VISIBLE for exactly the same members. That trades invisible for always-on,
 * the owner rejected it on the board, and `TaskCardActions.tsx` carries the
 * story.
 *
 * The answer is `reveal-on-hover` in `globals.css`, an `@utility` whose rules
 * carry no media query at all.
 *
 * ⚠️ **This is a text gate, and it cannot see rendering.** `vitest.config.ts`
 * is `environment: "node"`: no DOM, no layout, no matchMedia. What it pins is
 * that nobody writes the broken idiom again. That the utility actually
 * reveals is measured in a `hasTouch` browser —
 * `e2e/projects-card-strip.spec.ts` does it for the board (H-27: nothing in
 * CI runs `e2e/`).
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(SRC);
  return out.sort();
}

/**
 * Comments are stripped, because the files that explain this trap name the
 * broken spelling on purpose. A gate a code comment can trip teaches people
 * not to comment — `conformance.test.ts` learned that on `TaskPanel.tsx`.
 */
const strip = (text: string) =>
  text
    .replace(/(?<=^|[\s{(])\/\*[\s\S]*?\*\//g, "")
    .replace(/(?<![:"'/])\/\/[^\n]*/g, "");

describe("hover-revealed controls reach a touch-capable display", () => {
  it("nothing reveals with `group-hover:opacity-100`", () => {
    const offenders = sourceFiles().filter((rel) =>
      /group-hover:opacity-100/.test(strip(readFileSync(join(SRC, rel), "utf8"))),
    );
    expect(
      offenders,
      "Tailwind wraps `group-hover:` in @media (hover: hover), which never " +
        "matches on a touch-capable display — the control is invisible there " +
        "forever. Use the `reveal-on-hover` utility from globals.css.",
    ).toEqual([]);
  });

  it("nothing pins itself visible with an @media (hover: none) escape", () => {
    const offenders = sourceFiles().filter((rel) =>
      /\[@media\(hover:none\)\]:opacity-100/.test(
        strip(readFileSync(join(SRC, rel), "utf8")),
      ),
    );
    expect(
      offenders,
      "Pinning the control ON for touch-capable members trades invisible for " +
        "always-visible. The owner rejected that on the Projects board. Use " +
        "`reveal-on-hover`, which carries no media query.",
    ).toEqual([]);
  });

  it("the utility it points at actually exists, and carries no media query", () => {
    // The tripwire. Without this the two rules above stay green while the
    // class they recommend does nothing, which is the worse failure: every
    // control silently hidden and a suite reporting health.
    const css = readFileSync(join(SRC, "app", "globals.css"), "utf8");
    const at = css.indexOf("@utility reveal-on-hover");
    expect(at, "globals.css no longer defines `reveal-on-hover`").toBeGreaterThan(-1);

    const body = css.slice(at, css.indexOf("\n}", at));
    expect(body).toContain(".group:hover &");
    expect(body).toContain(":focus-visible");
    expect(
      body,
      "a media query here reintroduces the whole defect",
    ).not.toContain("@media");
  });
});
