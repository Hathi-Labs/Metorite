/**
 * STU-D23a fence: the touch size of the floor view.
 *
 * The `pointer-coarse` variant of `globals.css` matches a phone or a small tablet (a media
 * query, owner decision 2026-11-01). The floor view of a Studio app adds a second branch: any
 * element inside `[data-floor]`. So a tablet in landscape (1024 px and up) gets the touch size
 * in the floor view, and nothing outside it changes.
 *
 * Mutation 1: remove the `[data-floor]` branch. "has a branch for the floor root" and "emits both
 * branches" turn red.
 * Mutation 2: edit the media query. "keeps the media branch byte for byte" turns red.
 * Mutation 3: put `data-floor` on `<html>` or on a second component. "only the app frame sets
 * data-floor" turns red.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { compile } from "tailwindcss";
import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const GLOBALS = readFileSync(join(SRC, "app", "globals.css"), "utf8").replace(/\r\n/g, "\n");

const MEDIA = "@media (hover: none) and (pointer: coarse) and (max-width: 1023px)";
const FLOOR = "&:where([data-floor], [data-floor] *)";

/** The body of the `pointer-coarse` variant, from its open brace to the brace that closes it. */
function variantBlock(css: string): string {
  const head = "@custom-variant pointer-coarse {";
  const start = css.indexOf(head);
  if (start === -1) throw new Error("No block form of the pointer-coarse variant in globals.css");
  let depth = 0;
  for (let i = start + head.length - 1; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") depth -= 1;
    if (depth === 0) return css.slice(start, i + 1);
  }
  throw new Error("The pointer-coarse variant has no closing brace");
}

describe("the pointer-coarse variant", () => {
  const block = variantBlock(GLOBALS);

  it("is declared once", () => {
    expect(GLOBALS.match(/@custom-variant pointer-coarse\b/g)).toHaveLength(1);
  });

  it("keeps the media branch byte for byte", () => {
    expect(block).toContain(`${MEDIA} {\n    @slot;\n  }`);
  });

  it("has a branch for the floor root", () => {
    expect(block).toContain(`${FLOOR} {\n    @slot;\n  }`);
  });

  it("emits both branches for a touch class, and the glass touch class still wins over the glass size", async () => {
    const glass = '@custom-variant glass (&:where([data-look="glass"], [data-look="glass"] *));';
    const css = `@theme { --spacing: 0.25rem; }\n${glass}\n${block}\n@tailwind utilities;`;
    const c = await compile(css, { base: "/", loadStylesheet: async () => ({ content: "", base: "/", path: "" }) });
    const out = c.build(["glass:h-8", "glass:pointer-coarse:h-10", "pointer-coarse:h-11"]);
    const rule = (name: string) => out.slice(out.indexOf(name), out.indexOf("\n}", out.indexOf(name)));
    for (const name of [".glass\\:pointer-coarse\\:h-10", ".pointer-coarse\\:h-11"]) {
      expect(rule(name)).toContain(MEDIA);
      expect(rule(name)).toContain(FLOOR);
    }
    // Same specificity, so order decides: the touch size comes after the glass size.
    expect(out.indexOf(".glass\\:pointer-coarse\\:h-10")).toBeGreaterThan(out.indexOf(".glass\\:h-8"));
  });
});

describe("the floor root", () => {
  function files(dir: string): string[] {
    const out: string[] = [];
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) out.push(...files(full));
      else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(full);
    }
    return out;
  }

  it("only the app frame sets data-floor", () => {
    const setters = files(SRC)
      .filter((f) => /data-floor\s*=|["']data-floor["']\s*[:\]]/.test(readFileSync(f, "utf8")))
      .map((f) => relative(SRC, f).split(sep).join("/"));
    expect(setters).toEqual(["components/studio/StudioAppFrame.tsx"]);
  });
});
