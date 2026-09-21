/**
 * A raw `<button>` that LOOKS like a control, counted and ratcheted.
 *
 * Spec: `DESIGN_SYSTEM.md` §3 — *"Not every `<button>` is a control. A
 * clickable card or list row is a button element for accessibility, and
 * those legitimately stay raw — the rule is about things that look like
 * controls."*
 *
 * **That sentence is right and was too vague to act on.** A count of raw
 * `<button>` in the Projects app came to 54 on 2026-09-21 and was written up
 * as 54 defects. Classified, it is not:
 *
 * | Kind | Count | Verdict |
 * |---|---|---|
 * | Row, menu item, clickable card (`text-left` / `w-full`) | 29 | Legitimate, per §3 |
 * | Colour swatch (`h-4 w-4 rounded-full` + a hue) | 5 | Legitimate — a swatch has no control personality to inherit |
 * | Control-shaped: a fill, or a border plus a radius | 5 | **Defect** |
 * | Icon-only affordance in a dense row | ~9 | **Defect** — `Button size="icon-xs" variant="ghost"` exists for exactly this |
 * | Bare text toggle, sort header | ~6 | Row-ish. Left alone |
 *
 * So the debt is real and it is about a third of the headline number. This
 * test counts the category that matters and **ratchets it**: the number may
 * fall and never rise. One mega-sweep across the densest app in the tree is
 * how a cohesion fix becomes a regression; a ratchet lets it come down a
 * file at a time, and stops a new one arriving meanwhile.
 *
 * ⚠️ **The classifier is deliberately narrow.** It flags a solid fill
 * (`bg-primary`, `bg-destructive`) or a border-with-radius, and only when
 * the element is not a row. A wider net would sweep in the 29 legitimate
 * rows, the test would be argued with instead of obeyed, and somebody would
 * delete it — which is the failure a fence has to design against.
 */

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const APP = path.join(__dirname, "..", "app");

/**
 * The ratchet, measured 2026-09-21. Lower it when you convert one. Never
 * raise it: a new control-shaped raw button is what this exists to stop.
 */
const BUDGET: Record<string, number> = {
  // 5 on 2026-09-21, then 3, now 0. The last three were toggles blocked on
  // a primitive that did not exist; `Button` gained a `selected` prop the
  // same day (H-149) and all three converted.
  //
  // ⚠️ **Zero is the interesting number.** A budget above zero is debt
  // somebody has to remember; a budget at zero is a rule. Raising it is a
  // deliberate act with a reason, which is the conversation this file
  // exists to force.
  projects: 0,
};

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  const walk = (d: string) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) walk(full);
      else if (e.name.endsWith(".tsx")) out.push(full);
    }
  };
  walk(dir);
  return out;
}

/** The className of every raw `<button>`'s opening tag, brace-aware. */
function rawButtonClasses(source: string): string[] {
  const found: string[] = [];
  const re = /<button\b/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source))) {
    let i = m.index + m[0].length;
    let depth = 0;
    for (; i < source.length; i++) {
      const ch = source[i];
      if (ch === "{") depth++;
      else if (ch === "}") depth--;
      else if (ch === ">" && depth === 0) break;
    }
    const tag = source.slice(m.index, i);
    const cm =
      /className="([^"]*)"/.exec(tag) ??
      /className=\{`([^`]*)`\}/.exec(tag) ??
      /className=\{([^}]*)\}/.exec(tag);
    found.push(cm ? cm[1].replace(/\s+/g, " ") : "");
  }
  return found;
}

/** Does this look like a control rather than a row, a card or a swatch? */
export function looksLikeAControl(cls: string): boolean {
  if (!cls) return false;
  // A row or a clickable card. §3 says these stay raw.
  if (/\btext-left\b|\bw-full\b/.test(cls)) return false;
  // A colour swatch: a small circle whose whole job is to be a colour.
  if (/\bh-4 w-4\b/.test(cls) && /\brounded-full\b/.test(cls)) return false;
  // Already wearing the shared personality.
  if (cls.includes("cc-control")) return false;
  if (/\bbg-(primary|destructive)\b/.test(cls)) return true;
  return /\bborder\b/.test(cls) && /\brounded/.test(cls);
}

describe("raw buttons that look like controls", () => {
  it("finds files at all", () => {
    expect(tsxFiles(path.join(APP, "projects")).length).toBeGreaterThan(10);
  });

  for (const [app, budget] of Object.entries(BUDGET)) {
    it(`${app}: no more than ${budget}, and the number only goes down`, () => {
      const offenders: string[] = [];
      for (const file of tsxFiles(path.join(APP, app))) {
        for (const cls of rawButtonClasses(fs.readFileSync(file, "utf-8"))) {
          if (looksLikeAControl(cls)) {
            offenders.push(`${path.relative(APP, file)}: ${cls.slice(0, 70)}`);
          }
        }
      }
      expect(
        offenders.length,
        `Use <Button> for a control (DESIGN_SYSTEM §3). If you CONVERTED one, ` +
          `lower BUDGET.${app}. Offenders:\n${offenders.join("\n")}`,
      ).toBeLessThanOrEqual(budget);
    });
  }

  it("classifies the four kinds the way §3 does", () => {
    // A row and a clickable card stay raw.
    expect(looksLikeAControl("flex w-full items-center text-left rounded")).toBe(false);
    // A swatch is a colour, not a control.
    expect(looksLikeAControl("h-4 w-4 rounded-full bg-sky-500")).toBe(false);
    // A solid fill is a control wherever it appears.
    expect(looksLikeAControl("rounded-md bg-primary px-2 py-1")).toBe(true);
    // So is an outlined one.
    expect(looksLikeAControl("rounded-md border border-border px-2 py-1")).toBe(true);
    // An unstyled button is somebody's wrapper, not a control.
    expect(looksLikeAControl("")).toBe(false);
  });
});
