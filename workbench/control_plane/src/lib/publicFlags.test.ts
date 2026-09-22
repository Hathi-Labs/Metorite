/**
 * Every `NEXT_PUBLIC_` flag is read in the one form Next can inline.
 *
 * ## The bug this exists for
 *
 * Next replaces the **literal member expression** `process.env.NEXT_PUBLIC_X`
 * with its value when it builds the browser bundle. It does not replace
 * anything else. A read through a defaulted parameter —
 *
 *     function enabled(env: Record<string, string | undefined> = process.env) {
 *       return env.NEXT_PUBLIC_X === "1";
 *     }
 *
 * — is not inlined, and in a browser `process.env` is the `{}` polyfill. So
 * the flag is `undefined`, the feature is permanently off, and **every test
 * still passes**, because each test hands the function an env object of its
 * own. Nothing in the suite is looking at the shape the app actually uses.
 *
 * The Projects chat shipped in that form, and the S1 review found it in the
 * build output on 2026-09-22. H-158 recorded that two more readers carried
 * it: `previewAppsVisible` in `nav.ts`, and `lensEnabled` in
 * `tasks/lib/lens.ts`. Both were fixed on 2026-09-23.
 *
 * ⚠️ **`lensEnabled` is the D53 cutover switch** — it decides whether the
 * Tasks app reads `pm_tasks` or the retired `gtd_items`. Measured on
 * 2026-09-23, neither flag was set on the box, so nothing was broken yet.
 * That is the point. Flip it after the S3b backfill, watch nothing change,
 * and the obvious reading is "the backfill did not work".
 *
 * ## Why this sweeps the tree instead of living beside each reader
 *
 * `projectApps.test.ts` already carries this assertion for its own module,
 * and it stays there — that is that module's behavioural suite, and it
 * answers "is `chatEnabled` correct". This answers a different question:
 * "does any file in the tree carry the shape". A rule that only fences the
 * three sites we know about does not fence the fourth.
 */

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC = path.join(__dirname, "..");

/** The broken shape, in the spellings prettier and a human both produce. */
const DEFAULTED_ENV_PARAM =
  /env\s*:\s*Record<\s*string\s*,\s*string\s*\|\s*undefined\s*>\s*=\s*process\.env/;

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Comments stripped FIRST.
 *
 * Both readers now EXPLAIN the broken shape in a comment directly above the
 * fixed one, so a fence that reads raw source finds the defect in the very
 * text warning against it. That is not hypothetical — the migration rename
 * fence and the heading fence both shipped with this bug, and both were
 * corrected the same way.
 */
function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*/g, "");
}

function sourcesNamingAFlag(): Array<{ rel: string; text: string }> {
  return walk(SRC)
    .map((file) => ({
      rel: path.relative(SRC, file).split(path.sep).join("/"),
      text: code(fs.readFileSync(file, "utf8")),
    }))
    .filter((f) => /NEXT_PUBLIC_[A-Z0-9_]+/.test(f.text));
}

describe("every NEXT_PUBLIC flag reaches the browser", () => {
  it("finds the flag readers at all", () => {
    // A sweep that matches nothing passes every assertion below vacuously.
    expect(sourcesNamingAFlag().length).toBeGreaterThanOrEqual(4);
  });

  it("no file reads a flag through a defaulted `env = process.env`", () => {
    const offenders = sourcesNamingAFlag()
      .filter((f) => DEFAULTED_ENV_PARAM.test(f.text))
      .map((f) => f.rel);
    expect(
      offenders,
      "these read NEXT_PUBLIC_* through a default parameter, which Next does " +
        "NOT inline — the flag is undefined in the browser and the feature is " +
        "permanently off. Take `env?: Record<…>` and read the literal when it " +
        "is absent, as `projectApps.ts::chatEnabled` does.",
    ).toEqual([]);
  });

  it("each flag the app branches on is read through its literal somewhere", () => {
    // The three that turn a feature on or off. `GATEWAY_URL`, `WORKBENCH_URL`
    // and `EMAIL_DEMO` are read at module scope as literals already and need
    // no entry here; the point of this list is the flags a REVIEWER would
    // flip and then wonder about.
    const BRANCHING_FLAGS = [
      "NEXT_PUBLIC_PROJECTS_CHAT",
      "NEXT_PUBLIC_TASKS_LENS",
      "NEXT_PUBLIC_SHOW_PREVIEW_APPS",
    ];
    const all = sourcesNamingAFlag()
      .map((f) => f.text)
      .join("\n");
    for (const flag of BRANCHING_FLAGS) {
      expect(all, flag).toContain(`process.env.${flag}`);
    }
  });
});
