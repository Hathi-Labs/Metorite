import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * H-27 — the browser gate, and the register of what it does not cover.
 *
 * 🔴 **Nothing ran `e2e/` for an unknown period, and nothing said so.**
 * D-PM-21 makes a real browser the only fence for UI behaviour here, because
 * `vitest.config.ts` is `environment: "node"` and never collects a `.tsx`. The
 * browser suite is therefore the whole of the UI's coverage, and it had rotted
 * to 62 passed / 33 failed / 18 fixme by the time anybody ran it (2026-09-22).
 *
 * `pr-check.yml` now runs the `ci` project, whose `testIgnore` quarantines the
 * failing specs by name. **This file fences the quarantine**, because the
 * register is the part most likely to go quietly wrong:
 *
 * * an entry can outlive the file it names, and then it silently covers nothing
 * * the list can grow until the gate runs no tests at all and passes vacuously
 * * the workflow can stop invoking the project and nothing would fail
 *
 * ⚠️ The last one is not hypothetical paranoia. Three separate fences written
 * in this repo in one day passed on their own counterexample. A gate that
 * cannot fail is the defect it was built to prevent.
 */

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));

const CONFIG = readFileSync(HERE("../../playwright.config.ts"), "utf8");
const WORKFLOW = readFileSync(
  HERE("../../../../.github/workflows/pr-check.yml"),
  "utf8"
);
/** The workflow with LF endings, so a Windows checkout reads it as CI does. */
const LF = WORKFLOW.replace(/\r\n/g, "\n");
const SPECS = readdirSync(HERE("../../e2e")).filter((f) => f.endsWith(".spec.ts"));

/** Each job of `pr-check.yml`, keyed by its id, as the raw text of its block. */
function jobs(): Record<string, string> {
  const body = LF.slice(LF.indexOf("\njobs:\n"));
  const out: Record<string, string> = {};
  const heads = [...body.matchAll(/^ {2}([a-z0-9-]+):$/gm)];
  heads.forEach((m, i) => {
    out[m[1]] = body.slice(m.index, heads[i + 1]?.index ?? body.length);
  });
  return out;
}

/** The `testIgnore` entries declared on the `ci` project, as bare filenames. */
function quarantined(): string[] {
  const from = CONFIG.indexOf('name: "ci"');
  expect(from, 'the "ci" project is missing from playwright.config.ts').toBeGreaterThan(-1);
  const block = CONFIG.slice(from, CONFIG.indexOf("use: {", from));
  return [...block.matchAll(/"\*\*\/([^"]+\.spec\.ts)"/g)].map((m) => m[1]);
}

describe("the browser gate CI runs", () => {
  it("is the project the workflow actually invokes", () => {
    // Renaming the project without renaming it here would leave a job that
    // runs Playwright's default — every spec, including the red ones — and a
    // permanently red gate reads as "e2e is broken again" rather than as this.
    expect(WORKFLOW).toMatch(/playwright test --project=ci/);
  });

  it("blocks, rather than reporting", () => {
    // 🔴 The failure this whole entry is about, one level up. A browser check
    // that cannot fail the run is advisory by accident (R7).
    const job = WORKFLOW.slice(WORKFLOW.indexOf("\n  e2e:"));
    const step = job.slice(0, job.indexOf("secret-scan:"));
    expect(step).toContain("npx playwright test --project=ci");
    expect(step).not.toMatch(/continue-on-error:\s*true/);
  });

  it("skips only its push copy, and still runs on every pull_request", () => {
    // The push copy duplicates the PR run and adds no fence at the merge
    // button, because a conflicted PR cannot merge. `!= 'push'` and not
    // `== 'pull_request'`, so a trigger added later still runs the suite.
    expect(jobs().e2e).toMatch(/^ {4}if: github\.event_name != 'push'$/m);
  });

  it("lets only the slow suites skip a push", () => {
    // 🔴 The push trigger exists so a CONFLICTED PR still runs the R1
    // migration-prefix guard. The two slow suites (the unit shards and this
    // one) skip their push copy, because a conflicted PR cannot merge. A
    // job-level `if:` on any other job could switch a fence off on the one
    // run that watches a conflicted PR.
    expect(LF).toMatch(/^ {2}push:$/m);
    const all = jobs();
    const slow = new Set(["e2e", "test", "unit-tests"]);
    for (const name of slow) expect(Object.keys(all)).toContain(name);
    for (const [name, body] of Object.entries(all)) {
      if (slow.has(name)) continue;
      expect(body, `job "${name}" has a job-level if:`).not.toMatch(/^ {4}if:/m);
    }
  });

  it("runs the migration-prefix guard in lint, which runs on push", () => {
    // The guard left the unit job when the unit job stopped running on push.
    expect(jobs().lint).toContain("pytest tests/unit/test_migration_prefixes.py");
    expect(jobs().lint).not.toMatch(/^ {4}if:/m);
  });

  it("has a cap above the measured run", () => {
    // 🔴 The cap of 20 was the fault: the job takes about 20 minutes, so the
    // cap cancelled green runs (measured 2026-10-15). Lower it again only with
    // a new measured duration.
    const cap = Number(/^ {4}timeout-minutes: (\d+)$/m.exec(jobs().e2e)?.[1]);
    expect(cap).toBeGreaterThanOrEqual(30);
  });

  it("keeps the report when it goes red", () => {
    // An undiagnosable gate is one somebody deletes instead of fixing.
    const job = WORKFLOW.slice(WORKFLOW.indexOf("\n  e2e:"));
    expect(job.slice(0, job.indexOf("secret-scan:"))).toMatch(
      /if:\s*failure\(\)[\s\S]*upload-artifact/
    );
  });
});

describe("the quarantine register", () => {
  it("names only specs that exist", () => {
    // A stale entry covers nothing and reads as coverage. It happens the first
    // time somebody renames a spec.
    for (const name of quarantined()) {
      expect(SPECS, `${name} is quarantined but no such spec exists`).toContain(name);
    }
  });

  it("names each spec once", () => {
    const names = quarantined();
    expect(new Set(names).size).toBe(names.length);
  });

  it("does NOT gag the local run", () => {
    // ⚠️ `--project=chromium` must keep running everything. The rot has to
    // stay visible to the person who can fix it — a quarantine that hides its
    // own contents is the same defect as a suite nothing runs.
    const from = CONFIG.indexOf('name: "chromium"');
    expect(from).toBeGreaterThan(-1);
    const block = CONFIG.slice(from, CONFIG.indexOf("},", CONFIG.indexOf("use: {", from)));
    expect(block).not.toContain("testIgnore");
  });

  it("leaves the gate something to run", () => {
    // 🔴 **The one that matters.** Quarantine every spec and the job passes in
    // four seconds having tested nothing — green, and worthless. Assert the
    // gated set by name rather than by count, so growing the list past these
    // fails here instead of silently emptying the gate.
    const gated = SPECS.filter((s) => !quarantined().includes(s));
    expect(gated.length).toBeGreaterThanOrEqual(8);
    for (const must of [
      "projects-card-strip.spec.ts",
      "projects-move-picker.spec.ts",
      "projects-stale-after-write.spec.ts",
      "projects-timeline-autoscroll.spec.ts",
      "toast.spec.ts",
      "status-accent.spec.ts",
      "sandbox-theming.spec.ts",
      "react-artifact.spec.ts",
    ]) {
      expect(gated, `${must} was green on 2026-09-22 and must stay gated`).toContain(must);
    }
  });
});
