/**
 * The Metorite comet and the sidebar's foot signature (owner, 2026-10-11).
 *
 * The owner moved our own mark out of the shell bar and to the foot of the
 * left sidebar. These checks fail on the wrong builds that look right:
 *   1. A comet path that drifted from `brand/`, or a second copy of it.
 *   2. Gradient stops that ignore the colour mode, or the wrong master's.
 *   3. Two marks on one page that share one gradient id.
 *   4. A signature that turned into a control: a link, a button, a hover fill.
 *   5. A signature that left its place over the account row.
 * The geometry (centred in the folded rail) is `e2e/sidebar-signature.spec.ts`.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MetoriteMark, MetoriteSignature } from "./MetoriteMark";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const BRAND = fileURLToPath(new URL("../../../../brand/", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
const master = (name: string) => readFileSync(join(BRAND, name), "utf8");

const pathOf = (svg: string) => /<path\b[^>]*\sd="([^"]+)"/.exec(svg)?.[1] ?? "";
const stopsOf = (svg: string) => [...svg.matchAll(/stop-color="(#[0-9a-fA-F]{6})"/g)].map((m) => m[1].toLowerCase());
const html = (props: Parameters<typeof MetoriteMark>[0] = {}) => renderToStaticMarkup(createElement(MetoriteMark, props));
const classes = (out: string) => [...out.matchAll(/<stop\b[^>]*class="([^"]*)"/g)].map((m) => m[1].replace(/&amp;/g, "&"));

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
    expect(holders).toEqual(["components/MetoriteMark.tsx"]);
  });
});

describe("the gradient follows the colour mode", () => {
  const lightStops = stopsOf(master("metorite-logo.svg"));
  const darkStops = stopsOf(master("metorite-logo-dark.svg"));
  const hexes = (cls: string) => [...cls.matchAll(/#[0-9a-f]{6}/g)].map((m) => m[0]);

  it("pinned light draws the light master's stops", () => {
    expect(classes(html({ mode: "light" })).map((c) => hexes(c)[0])).toEqual(lightStops);
  });

  it("pinned dark draws the dark master's stops", () => {
    expect(classes(html({ mode: "dark" })).map((c) => hexes(c)[0])).toEqual(darkStops);
  });

  it("unpinned, dark is the base and `.light` swaps in the light stops", () => {
    const cls = classes(html());
    expect(cls).toHaveLength(3);
    cls.forEach((c, i) => {
      expect(c.split(" ")).toEqual([`[stop-color:${darkStops[i]}]`, `[.light_&]:[stop-color:${lightStops[i]}]`]);
    });
  });

  it("two marks on one page never share a gradient id", () => {
    const out = renderToStaticMarkup(createElement(Fragment, null, createElement(MetoriteMark), createElement(MetoriteMark)));
    const ids = [...out.matchAll(/<linearGradient id="([^"]+)"/g)].map((m) => m[1]);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
    for (const id of ids) expect(out).toContain(`fill="url(#${id})"`);
  });

  it("is decorative unless it is given a name", () => {
    expect(html()).toContain('aria-hidden="true"');
    const named = html({ label: "Metorite" });
    expect(named).toContain('role="img"');
    expect(named).toContain('aria-label="Metorite"');
  });
});

describe("the signature is a signature, not a control", () => {
  const open = renderToStaticMarkup(createElement(MetoriteSignature));
  const folded = renderToStaticMarkup(createElement(MetoriteSignature, { collapsed: true }));

  it("open, it shows the comet and the word, in muted text", () => {
    expect(open).toContain("data-metorite-mark");
    expect(open.replace(/<[^>]*>/g, "")).toBe("Metorite");
    expect(open).toContain("text-muted-foreground");
  });

  it("folded, it shows the comet alone, named by a native title", () => {
    expect(folded).toContain("data-metorite-mark");
    expect(folded.replace(/<[^>]*>/g, "")).toBe("");
    expect(folded).toContain('title="Metorite"');
    expect(folded).toContain('aria-label="Metorite"');
    expect(folded).toMatch(/class="flex justify-center/);
  });

  it("is never a link, a button, a focus stop or a hover fill", () => {
    for (const out of [open, folded]) {
      expect(out).not.toMatch(/<a\b|<button\b|tabindex|href=/i);
      expect(out).not.toMatch(/hover:/);
    }
  });
});

describe("the signature sits just over the account row", () => {
  it("desktop: the sidebar draws it before SidebarAccountFooter, open and folded", () => {
    const src = read("components/Sidebar.tsx");
    const sig = src.indexOf("<MetoriteSignature collapsed={collapsed}");
    const foot = src.indexOf("<SidebarAccountFooter");
    expect(sig).toBeGreaterThan(0);
    expect(foot).toBeGreaterThan(sig);
    // Nothing else is drawn between the two.
    expect(src.slice(sig, foot)).not.toMatch(/<(?!\/?MetoriteSignature)[A-Za-z]/);
  });

  it("phone: the drawer draws it before DrawerAccountFoot", () => {
    const src = read("components/AppShell.tsx");
    const sig = src.indexOf("<MetoriteSignature");
    const foot = src.indexOf("<DrawerAccountFoot");
    expect(sig).toBeGreaterThan(0);
    expect(foot).toBeGreaterThan(sig);
    expect(src.slice(sig, foot)).not.toMatch(/<(?!\/?MetoriteSignature)[A-Za-z]/);
  });
});
