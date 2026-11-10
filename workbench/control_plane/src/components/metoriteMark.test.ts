/**
 * The Metorite comet (owner, 2026-10-11). One solid colour, no gradient
 * (owner, 2026-10-30).
 *
 * These checks fail on the wrong builds that look right:
 *   1. A comet path that drifted from `brand/`, or a second copy of it.
 *   2. A fill that ignores the colour mode, or the wrong master's.
 *   3. A gradient that came back.
 * Where the comet sits is `lib/shell/shellAccount.test.ts` and
 * `e2e/shell-signature.spec.ts`.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MetoriteMark } from "./MetoriteMark";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const BRAND = fileURLToPath(new URL("../../../../brand/", import.meta.url));
const master = (name: string) => readFileSync(join(BRAND, name), "utf8");

const pathOf = (svg: string) => /<path\b[^>]*\sd="([^"]+)"/.exec(svg)?.[1] ?? "";
const fillOf = (svg: string) => /<path\b[^>]*\sfill="(#[0-9a-fA-F]{6})"/.exec(svg)?.[1].toLowerCase() ?? "";
const html = (props: Parameters<typeof MetoriteMark>[0] = {}) => renderToStaticMarkup(createElement(MetoriteMark, props));
const pathClass = (out: string) => (/<path\b[^>]*class="([^"]*)"/.exec(out)?.[1] ?? "").replace(/&amp;/g, "&");

describe("the comet is the brand master's, once", () => {
  it("draws the path of both masters, verbatim", () => {
    const light = pathOf(master("metorite-logo.svg"));
    const dark = pathOf(master("metorite-logo-dark.svg"));
    expect(light.length).toBeGreaterThan(1000);
    expect(dark).toBe(light);
    expect(pathOf(html())).toBe(light);
  });

  it("keeps the masters' viewBox, so the shape is not squashed", () => {
    expect(master("metorite-logo.svg")).toContain('viewBox="0 0 918 933"');
    expect(html()).toContain('viewBox="0 0 918 933"');
  });

  /**
   * The one other holder, with its reason. `app/icon.svg` is the browser's tab
   * icon (#854). Next serves it as a file, outside the page, so it cannot
   * import a component. `brand/README.md` documents it. A new holder fails.
   */
  const ALLOWED: Record<string, string> = {
    "app/icon.svg": "the browser's tab icon, served outside the page; brand/README.md documents it",
  };

  it("no other source file carries a copy of the path", () => {
    const head = pathOf(master("metorite-logo.svg")).slice(0, 40);
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(dir)) {
        const full = join(dir, e);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.(tsx?|css|svg)$/.test(e) && !/\.test\.tsx?$/.test(e)) files.push(full);
      }
    };
    walk(SRC);
    const holders = files.filter((f) => readFileSync(f, "utf8").includes(head)).map((f) => relative(SRC, f).split(sep).join("/"));
    expect(holders.filter((h) => !(h in ALLOWED))).toEqual(["components/MetoriteMark.tsx"]);
    // An allowed holder that stopped holding the path is a stale entry.
    for (const h of Object.keys(ALLOWED)) expect(holders, h).toContain(h);
  });
});

describe("the solid fill follows the colour mode", () => {
  const lightFill = fillOf(master("metorite-logo.svg"));
  const darkFill = fillOf(master("metorite-logo-dark.svg"));

  it("the masters are solid black for light and solid white for dark (owner, 2026-10-30)", () => {
    expect(lightFill).toBe("#000000");
    expect(darkFill).toBe("#ffffff");
    for (const name of ["metorite-logo.svg", "metorite-logo-dark.svg", "favicon.svg"]) {
      expect(master(name), name).not.toMatch(/Gradient|<stop\b|url\(#/);
    }
  });

  it("the tab icon is black, and white for a dark browser frame", () => {
    const icon = readFileSync(join(SRC, "app", "icon.svg"), "utf8");
    expect(icon).not.toMatch(/Gradient|<stop\b|url\(#/);
    expect(icon).toContain(`.a{fill:${lightFill}}`);
    expect(icon).toContain(`@media (prefers-color-scheme:dark){.a{fill:${darkFill}}}`);
    expect(icon).toMatch(/<path class="a" fill-rule="evenodd"/);
  });

  it("draws no gradient", () => {
    for (const mode of [undefined, "light", "dark"] as const) {
      expect(html({ mode })).not.toMatch(/Gradient|<stop\b|url\(#/);
    }
  });

  it("pinned light draws the light master's fill", () => {
    expect(pathClass(html({ mode: "light" }))).toBe(`[fill:${lightFill}]`);
  });

  it("pinned dark draws the dark master's fill", () => {
    expect(pathClass(html({ mode: "dark" }))).toBe(`[fill:${darkFill}]`);
  });

  it("unpinned, dark is the base and `.light` swaps in the light fill", () => {
    expect(pathClass(html()).split(" ")).toEqual([`[fill:${darkFill}]`, `[.light_&]:[fill:${lightFill}]`]);
  });

  it("is decorative unless it is given a name", () => {
    expect(html()).toContain('aria-hidden="true"');
    const named = html({ label: "Metorite" });
    expect(named).toContain('role="img"');
    expect(named).toContain('aria-label="Metorite"');
  });
});
