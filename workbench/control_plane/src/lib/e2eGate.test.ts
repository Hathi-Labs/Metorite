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
const SPECS = readdirSync(HERE("../../e2e")).filter((f) => f.endsWith(".spec.ts"));

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
      "projects-select-portal.spec.ts",
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
