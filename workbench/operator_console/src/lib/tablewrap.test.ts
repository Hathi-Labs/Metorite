// Every table scrolls INSIDE its own box, and none widens the document.
//
// 🔴 **The defect this exists for, measured 2026-09-20.** At 390px the AI
// usage board's organization table escaped its card and pushed the DOCUMENT
// to about 840px. A page that scrolls sideways does not move one table — it
// moves the nav, the page heading and every other panel with it, and the
// operator cannot tell which element is at fault.
//
// ⚠️ **One file already had the wrapper and twelve did not.** That is the
// shape of this bug: `.tablewrap` was added to `globals.css` and applied at
// the one place the reviewer happened to be looking. A CSS class nobody is
// required to use protects the file it was written in and nothing else.
//
// ⚠️ **Comments are stripped before matching.** A previous fence in this
// console matched its OWN explanatory comment and passed while the code it
// described was wrong. `catalog.test.ts` records that trap at "seven times
// and counting".

import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const APP = join(__dirname, "..", "app");

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...tsxFiles(p));
    else if (name.endsWith(".tsx")) out.push(p);
  }
  return out;
}

/** Source with every comment removed, so a fence cannot match its own prose. */
function code(path: string): string {
  return readFileSync(path, "utf8")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

describe("every table is wrapped", () => {
  const files = tsxFiles(APP).filter((f) => code(f).includes("<table"));

  it("finds the tables at all, so an empty sweep cannot pass", () => {
    // 🔴 Without this, deleting the glob makes the suite green.
    expect(files.length).toBeGreaterThanOrEqual(9);
  });

  it.each(files.map((f) => [f.slice(f.indexOf("app")), f]))(
    "%s keeps its table inside .tablewrap",
    (_label, path) => {
      const src = code(path as string);
      const tables = (src.match(/<table[\s>]/g) ?? []).length;
      // The wrapper must IMMEDIATELY precede the table. A `.tablewrap`
      // somewhere else in the file is not the same claim.
      const wrapped = (
        src.match(/<div className="tablewrap">\s*<table[\s>]/g) ?? []
      ).length;
      expect(wrapped).toBe(tables);
    },
  );
});
