/**
 * A heading comes from a component, not from whatever classes were to hand.
 *
 * Spec: `DESIGN_SYSTEM.md` · `AGENTS.md` rule 1 — every app is a projection of
 * one product, never a surface with its own look.
 *
 * **The product has three heading shapes, and this fence keeps each of them
 * in ONE place. They differ in CHROME, never in the title's size:**
 *
 * 1. `PageHeader` — the DOCUMENT header. No rule under it, at the top of a
 *    page that flows. The People app.
 * 2. `SettingsHeader` — the PANE header. Carries the back link out of a pane
 *    you navigated into, inside the bordered bar. Settings.
 * 3. The APP BAR — a slim `h-10` strip carrying a rail toggle and the app's
 *    name. Projects and Tasks. Deliberately not a page header, and it has no
 *    component yet.
 *
 * Both components take their title from `headingScale.ts`, so shape 1 and
 * shape 2 are the same size. Tab from Workload to Organisation and only the
 * chrome changes.
 *
 * **What was measured, and when.** On 2026-09-21 the `<h1>` across People,
 * Projects and Settings came in eight spellings, and `PageHeader` closed the
 * People app. On 2026-09-22 Settings still hand-rolled shape 2 in seven
 * files with four spellings — and `settings/appearance/page.tsx` had gone
 * further and declared a LOCAL component called `PageHeader`, a different
 * component wearing the shared one's name. `SettingsHeader` closed those,
 * and a visual pass then found the two components still disagreed about the
 * title's size. `headingScale.ts` closed that.
 *
 * ## Why a RATCHET and not a ban
 *
 * Forty-four files still carry a raw `<h1>`, across apps nobody has swept.
 * CLAUDE.md §5 says not to refactor the tree to conform, so a hard ban would
 * be wrong and would simply be deleted by whoever hit it first.
 *
 * So: the two swept apps are held at ZERO, and the rest is a budget that may
 * fall and never rise. Sweep an app, lower the number, and it cannot come
 * back.
 */

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC = path.join(__dirname, "..");

/**
 * The count of files outside the swept apps that still write their own
 * `<h1>`. **Measured 2026-09-22, down from 49. This may only ever go DOWN.**
 *
 * To lower it: convert a file's heading to `PageHeader` or `SettingsHeader`,
 * then set this to the new count. Never raise it — a new surface takes a
 * heading component, which is the whole point.
 */
const RAW_HEADING_BUDGET = 44;

/** The two components that legitimately contain the one `<h1>` each. */
const HEADING_COMPONENTS = ["PageHeader.tsx", "SettingsHeader.tsx"];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (entry.name.endsWith(".tsx")) out.push(full);
  }
  return out;
}

const RAW_H1 = /<h1[\s>]/;

/** Block and line comments, so prose about a heading is not a heading. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*/g, "");
}

function filesWithARawHeading(): string[] {
  return walk(SRC)
    .filter((f) => !HEADING_COMPONENTS.includes(path.basename(f)))
    .filter((f) => RAW_H1.test(fs.readFileSync(f, "utf8")))
    .map((f) => path.relative(SRC, f).split(path.sep).join("/"));
}

describe("the heading comes from a component", () => {
  it("finds the source at all", () => {
    // A walk that finds nothing makes every assertion below vacuous.
    expect(walk(SRC).length).toBeGreaterThan(100);
  });

  it("both heading components take the title scale from ONE constant", () => {
    // The residual cohesion gap the 2026-09-22 visual pass found: People's
    // title was `text-sm font-medium`, the same size as the tab label above
    // it, while Settings' was `text-base font-bold sm:text-lg`. One product,
    // two title sizes. `headingScale.ts` is now the only place either size
    // is written down.
    for (const name of HEADING_COMPONENTS) {
      const source = stripComments(
        fs.readFileSync(path.join(SRC, "components", name), "utf8"),
      );
      expect(source).toContain("HEADING_TITLE");
      expect(source).toContain("HEADING_SUBTITLE");
      // No component spells a text size of its own.
      expect(source).not.toMatch(/text-(xs|sm|base|lg|xl)/);
    }
  });

  it("both heading components exist and each holds exactly one <h1>", () => {
    for (const name of HEADING_COMPONENTS) {
      // Comments stripped FIRST: both docstrings say `<h1>` while explaining
      // themselves, and a fence that counts prose measures the wrong thing.
      // The same correction the migration no-op assertion needed.
      const source = stripComments(
        fs.readFileSync(path.join(SRC, "components", name), "utf8"),
      );
      expect(source.match(/<h1[\s>]/g) ?? []).toHaveLength(1);
    }
  });

  it("the People app writes no heading of its own", () => {
    const offenders = filesWithARawHeading().filter((f) =>
      f.startsWith("app/people"),
    );
    expect(offenders).toEqual([]);
  });

  it("Settings writes no PANE heading of its own", () => {
    // The two that remain are the same access-denied card, inside a centred
    // `rounded-xl border` panel. That is a THIRD thing — an empty state, not
    // the header of a pane — and the two agree with each other. Named here so
    // the exception is a decision rather than a leftover.
    const DENIED_CARD = [
      "app/settings/members/[email]/page.tsx",
      "app/settings/organization/OrganizationAdmin.tsx",
    ];
    const offenders = filesWithARawHeading()
      .filter((f) => f.startsWith("app/settings"))
      .filter((f) => !DENIED_CARD.includes(f));
    expect(offenders).toEqual([]);
  });

  it("the two access-denied cards still agree with each other", () => {
    const spellings = new Set<string>();
    for (const f of [
      "app/settings/members/[email]/page.tsx",
      "app/settings/organization/OrganizationAdmin.tsx",
    ]) {
      const source = fs.readFileSync(path.join(SRC, f), "utf8");
      for (const m of source.matchAll(/<h1 className="([^"]*)"/g)) {
        spellings.add(m[1]);
      }
    }
    expect([...spellings]).toEqual(["text-base font-semibold text-foreground"]);
  });

  it("the rest of the tree does not grow a new hand-rolled heading", () => {
    const offenders = filesWithARawHeading();
    expect(
      offenders.length,
      `${offenders.length} files write their own <h1>; the budget is ` +
        `${RAW_HEADING_BUDGET} and it may only fall. If you swept one, lower ` +
        `RAW_HEADING_BUDGET. If you added one, use PageHeader or ` +
        `SettingsHeader instead.\n${offenders.join("\n")}`,
    ).toBeLessThanOrEqual(RAW_HEADING_BUDGET);
  });

  it("nobody declares a SECOND component called PageHeader", () => {
    // `settings/appearance/page.tsx` did, until 2026-09-22. A local component
    // with the shared one's name is invisible at the call site — you have to
    // read the imports to find out which one you are looking at.
    const offenders = walk(SRC)
      .filter((f) => path.basename(f) !== "PageHeader.tsx")
      .filter((f) => /function\s+PageHeader\s*\(/.test(fs.readFileSync(f, "utf8")))
      .map((f) => path.relative(SRC, f).split(path.sep).join("/"));
    expect(offenders).toEqual([]);
  });
});
