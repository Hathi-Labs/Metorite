// A paired action row: one size, two weights, and a reason on every refusal.
//
// 🔴 **The owner reported this twice, five weeks apart.** First as "the button
// names seem to be all over the place". Then, on 2026-09-22, as *"the Save
// this order button is larger than the Undo button"* — and, in the same
// breath, *"I do not see a way in which I can save my changes."*
//
// Both come from the same habit: reaching for `.linklike` whenever an action
// is not the primary one. `.linklike` is a TERTIARY control, sized for a list
// row beside `↑` and `↓` — `font-size: 12px`, `padding: 5px 11px`,
// `margin: 0`. The base `button` is `14px`, `8px 14px`, `margin-top: 12px`.
// Put the two in one flex row and the primary stands ~9px taller and, because
// `align-items: center` centres the MARGIN box, sits ~6px lower.
//
// `button.secondary` is the right tool and already existed. It overrides
// background, border and colour, and inherits every metric.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const CSS = readFileSync(join(SRC, "app", "globals.css"), "utf8");

/** The surfaces the owner named, plus the one they are all FOR.
 *
 * ⚠️ `customers/[slug]` carries the "Add AI credits" panel. Every other page
 * here exists so that panel can be used correctly, so leaving it out would
 * fence the journey and not its destination. */
const PAGES = ["tiers", "pricing", "models", "providers", "customers"] as const;

function sourcesFor(page: string): [string, string][] {
  // ⚠️ Recursive, because `customers` holds its components one level down in
  // `[slug]/`. A flat read there would find one file and silently fence
  // nothing — the shape of green-but-checking-nothing this suite is about.
  const { readdirSync } = require("node:fs") as typeof import("node:fs");
  const out: [string, string][] = [];
  const walk = (dir: string, prefix: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (e.isDirectory()) walk(join(dir, e.name), `${prefix}${e.name}/`);
      else if (e.name.endsWith(".tsx"))
        out.push([`${prefix}${e.name}`, readFileSync(join(dir, e.name), "utf8")]);
    }
  };
  walk(join(SRC, "app", page), "");
  return out;
}

/** Every `<div className="...actions...">` block, crudely but sufficiently. */
function actionRows(src: string): string[] {
  const out: string[] = [];
  const re = /<div className="([^"]*actions[^"]*)">/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    const rest = src.slice(m.index + m[0].length);
    let depth = 1;
    let i = 0;
    while (i < rest.length && depth > 0) {
      if (rest.startsWith("<div", i)) depth += 1;
      else if (rest.startsWith("</div>", i)) depth -= 1;
      i += 1;
    }
    out.push(rest.slice(0, i));
  }
  return out;
}

/** The className of each `<button>` in a block, or "(default)". */
function buttonKinds(block: string): string[] {
  const kinds: string[] = [];
  const re = /<button\b((?:[^>"]|"[^"]*"|\{[^}]*\})*?)>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(block)) !== null) {
    const cls = /className="([^"]*)"/.exec(m[1]);
    kinds.push(cls ? cls[1] : "(default)");
  }
  return kinds;
}

describe("a paired action row uses ONE size", () => {
  for (const page of PAGES) {
    it(`/${page} never mixes a default button with a .linklike in one row`, () => {
      for (const [name, src] of sourcesFor(page)) {
        for (const block of actionRows(src)) {
          const kinds = buttonKinds(block);
          if (kinds.length < 2) continue;
          const hasPrimary = kinds.includes("(default)");
          const hasLinklike = kinds.some((k) => k.split(/\s+/).includes("linklike"));
          expect(
            hasPrimary && hasLinklike,
            `${page}/${name}: an action row pairs a full-size button with a ` +
              `.linklike (${kinds.join(", ")}). A peer action is ` +
              `button.secondary — same metrics, different weight.`,
          ).toBe(false);
        }
      }
    });
  }

  it("button.secondary overrides no METRIC, so a pair cannot drift", () => {
    // If somebody gives `.secondary` its own padding or font-size, the whole
    // fence above becomes decorative — the two buttons would carry different
    // classes AND different sizes again.
    const rule = /button\.secondary\s*\{([^}]*)\}/.exec(CSS);
    expect(rule, "button.secondary is gone — the fence above names it").not.toBe(
      null,
    );
    for (const prop of ["padding", "font-size", "margin", "height"]) {
      expect(
        rule![1],
        `button.secondary sets ${prop}, so it no longer matches its partner`,
      ).not.toContain(prop);
    }
  });

  it("⚠️ a paired row owns its spacing — a child may not push itself down", () => {
    // The base button carries `margin-top: 12px`. In a centred flex row that
    // offsets it from a sibling that has none, which is the other half of
    // what the owner saw.
    expect(CSS).toContain(".job-actions > button { margin-top: 0; }");
  });
});

describe("a control that refuses says why", () => {
  it("🔴 the tier Save explains the empty chain instead of greying out mutely", () => {
    // The owner emptied a chain to rebuild it and reported: "I do not see a
    // way in which I can save my changes." The refusal is correct — an empty
    // chain takes the tier off the air — so it stays, and it speaks.
    const src = readFileSync(join(SRC, "app", "tiers", "TierBoard.tsx"), "utf8");
    expect(src).toContain("This job has no model left");
    expect(src).toContain("Add at least one model");
  });

  it("every disabled button on these pages carries a title", () => {
    // A greyed control with no reason reads as broken, not as refusing.
    // `disabled={busy}` alone is exempt: an in-flight spinner explains itself.
    const bare: string[] = [];
    for (const page of PAGES) {
      for (const [name, src] of sourcesFor(page)) {
        const re = /<button\b((?:[^>"]|"[^"]*"|\{[^}]*\})*?)>/g;
        let m: RegExpExecArray | null;
        while ((m = re.exec(src)) !== null) {
          const attrs = m[1];
          const d = /disabled=\{([^}]*)\}/.exec(attrs);
          if (!d) continue;
          const cond = d[1].trim();
          // ⚠️ An IN-FLIGHT guard is exempt, and only that. A spinner explains
          // itself, so a title there would be noise on every surface. The test
          // is whether anything SURVIVES stripping the busy flags and the
          // operators that join them — `busy !== null || bulkBusy` reduces to
          // nothing and is exempt; `busy || chain.length === 0` does not.
          const residue = cond
            .replace(/\b(ctx\.)?(busy|bulkBusy)\b/g, "")
            .replace(/[!=]==?\s*null/g, "")
            .replace(/[()|!&\s]/g, "");
          if (residue === "") continue;
          if (!attrs.includes("title=")) bare.push(`${page}/${name}: ${cond}`);
        }
      }
    }
    expect(bare, `disabled with no reason given:\n  ${bare.join("\n  ")}`).toEqual(
      [],
    );
  });
});
