import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { pushOnPlainClick, pushView, replaceView } from "./history";

/**
 * The fence for `history.ts`: a CRM view change makes no server request.
 * Since CRM-C1 it also refuses a `next/link` `<Link>` under `src/app/crm`, and
 * an in-app `<a href>` whose plain click does not call `pushView` or
 * `replaceView`.
 * The runtime half is `e2e/crm-rail-held-request.spec.ts`, which holds the
 * server's answer and clicks the rail.
 *
 * crm-router-allowed: this file names the router calls it refuses, in its
 * patterns, its fixtures and its test names.
 */

describe("pushView and replaceView", () => {
  it("push a history entry with the view's address and nothing else", () => {
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    pushView("/crm?tab=leads", history);
    expect(history.pushState).toHaveBeenCalledWith(null, "", "/crm?tab=leads");
    expect(history.replaceState).not.toHaveBeenCalled();
  });

  it("replace the entry when the page corrects its own address", () => {
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    replaceView("/crm?tab=deals&view=v1", history);
    expect(history.replaceState).toHaveBeenCalledWith(null, "", "/crm?tab=deals&view=v1");
    expect(history.pushState).not.toHaveBeenCalled();
  });
});

// ── No CRM file uses the Next router ──────────────────────────────────────
//
// A `router.push` or `router.replace` waits on a React Server Components
// request before the view changes. With no timeout, a held request left the
// rail dead (crm_platform.md §18.6, owner report 2026-10-12).
//
// The scan covers every .ts and .tsx file under `src/app/crm/`, so a new
// component cannot bring the router back. A file that must use it says why
// in a comment that starts with `crm-router-allowed:`, as this file does.

const CRM_DIR = join(__dirname, "..");
/** The marker and a reason on the same line. A bare marker is not a reason. */
const ALLOW = /crm-router-allowed:[ \t]*\S.{10,}/;

/** The code without its comments, so a comment may name what it refuses. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

/** What the file does that the fence refuses. Empty when it is clean. */
function routerUses(src: string): string[] {
  if (ALLOW.test(src)) return [];
  const code = stripComments(src);
  const found: string[] = [];
  // `\buseRouter\b` also catches `useRouter as nav` and `nav.useRouter()`.
  if (/\buseRouter\b/.test(code)) found.push("useRouter");
  const call = code.match(/\b\w*[rR]outer\.(push|replace)\s*\(/g);
  if (call) found.push(...call.map((c) => c.replace(/\s*\($/, "")));
  return found;
}

/** A `next/link` import, in any form. The marker does not lift this one. */
function linkUses(src: string): string[] {
  const code = stripComments(src);
  const found: string[] = [];
  if (/from\s+["']next\/link["']/.test(code)) found.push("next/link");
  if (/(?:import|require)\s*\(\s*["']next\/link["']\s*\)/.test(code)) found.push("next/link");
  return found;
}

/** Each `<a ...>` opening tag in the source, with where it starts. */
function anchorTags(src: string): { tag: string; at: number }[] {
  const out: { tag: string; at: number }[] = [];
  const start = /<a(?=[\s>])/g;
  let match: RegExpExecArray | null;
  while ((match = start.exec(src))) {
    // Walk to the `>` that closes the tag. A `>` inside `{...}` or a string
    // is not it: `onClick={(e) => ...}` holds one.
    let depth = 0;
    let quote: string | null = null;
    let i = match.index + 2;
    for (; i < src.length; i++) {
      const c = src[i];
      if (quote) {
        if (c === quote) quote = null;
      } else if (c === '"' || c === "'" || c === "`") {
        quote = c;
      } else if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    out.push({ tag: src.slice(match.index, i + 1), at: match.index });
  }
  return out;
}

/** The marker of a link that leaves the app, with a reason, just before the tag. */
const EXTERNAL = /crm-link-external:[ \t]*\S.{10,}/;

/**
 * Each `<a href>` that would change the view through the browser (CRM-C1).
 * A plain click on it loads the page from the server. A link passes when its
 * click calls `pushView`, `replaceView` or `pushOnPlainClick`, when it opens
 * a new tab, when its href is `#`, `mailto:`, `tel:` or `http`, or when a
 * `crm-link-external:` comment with a reason stands just before it.
 */
function bareInternalLinks(src: string): string[] {
  return anchorTags(src)
    .filter(({ tag, at }) => {
      if (!/\bhref=/.test(tag)) return false;
      if (/\btarget="_blank"/.test(tag)) return false;
      if (/\bhref=(?:"|\{["'`])(?:#|mailto:|tel:|https?:)/.test(tag)) return false;
      if (/\bonClick=\{[\s\S]*\b(?:pushView|replaceView|pushOnPlainClick)\(/.test(tag)) return false;
      if (EXTERNAL.test(src.slice(Math.max(0, at - 300), at))) return false;
      return true;
    })
    .map(({ tag }) => tag.replace(/\s+/g, " ").slice(0, 80));
}

function crmFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return crmFiles(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("the fence's detector", () => {
  it("refuses useRouter, an aliased import, and both router calls", () => {
    expect(routerUses('import { useRouter } from "next/navigation";')).toEqual(["useRouter"]);
    expect(routerUses('import { useRouter as nav } from "next/navigation";')).toEqual(["useRouter"]);
    expect(routerUses("router.push(href, { scroll: false });")).toEqual(["router.push"]);
    expect(routerUses("appRouter.replace(href);")).toEqual(["appRouter.replace"]);
  });

  it("lets a comment name the router, and a file with a reason use it", () => {
    expect(routerUses("// never router.push here: it waits on the server")).toEqual([]);
    expect(routerUses("/** a `useRouter` push is slow */ const x = 1;")).toEqual([]);
    expect(
      routerUses("// crm-router-allowed: the page leaves the CRM for another route\nrouter.push(href);"),
    ).toEqual([]);
    // A marker with no reason is not a reason.
    expect(routerUses("// crm-router-allowed:\nrouter.push(href);")).toEqual(["router.push"]);
  });
});

describe("pushOnPlainClick", () => {
  const click = (over: Partial<MouseEvent> = {}) => ({
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    preventDefault: vi.fn(),
    ...over,
  });

  it("pushes the address on a plain left click, and stops the browser's load", () => {
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    const event = click();
    expect(pushOnPlainClick(event, "/crm?tab=settings&section=setup", history)).toBe(true);
    expect(history.pushState).toHaveBeenCalledWith(null, "", "/crm?tab=settings&section=setup");
    expect(event.preventDefault).toHaveBeenCalled();
  });

  it("leaves a middle click and each modifier click to the browser", () => {
    for (const over of [
      { button: 1 },
      { ctrlKey: true },
      { metaKey: true },
      { shiftKey: true },
      { altKey: true },
    ]) {
      const history = { pushState: vi.fn(), replaceState: vi.fn() };
      const event = click(over);
      expect(pushOnPlainClick(event, "/crm", history), JSON.stringify(over)).toBe(false);
      expect(history.pushState).not.toHaveBeenCalled();
      expect(event.preventDefault).not.toHaveBeenCalled();
    }
  });
});

describe("the in-app link detector", () => {
  it("refuses an internal <a href> with no pushView on its click", () => {
    expect(bareInternalLinks('<a href="/crm?tab=deals">Deals</a>')).toHaveLength(1);
    expect(bareInternalLinks("<a href={SETUP_HREF}>x</a>")).toHaveLength(1);
    expect(
      bareInternalLinks("<a\n  href={SETUP_HREF}\n  onClick={(e) => e.stopPropagation()}\n>x</a>"),
    ).toHaveLength(1);
  });

  it("allows a link that pushes, leaves the app, or names its reason", () => {
    expect(
      bareInternalLinks("<a href={SETUP_HREF} onClick={(event) => pushOnPlainClick(event, SETUP_HREF)}>x</a>"),
    ).toEqual([]);
    expect(bareInternalLinks('<a href={zoho} target="_blank" rel="noopener">x</a>')).toEqual([]);
    expect(bareInternalLinks('<a href="mailto:a@b.c">x</a>')).toEqual([]);
    expect(bareInternalLinks("<a href={`#${ANCHOR}`}>x</a>")).toEqual([]);
    expect(
      bareInternalLinks("// crm-link-external: a tel href leaves the app here\nreturn (<a href={value.href}>x</a>);"),
    ).toEqual([]);
    // A marker with no reason is not a reason.
    expect(bareInternalLinks("// crm-link-external:\n<a href={value.href}>x</a>")).toHaveLength(1);
    expect(bareInternalLinks("<abbr title=\"x\">y</abbr>")).toEqual([]);
  });
});

describe("the next/link detector", () => {
  it("refuses an import of next/link, and lets a comment name it", () => {
    expect(linkUses('import Link from "next/link";')).toEqual(["next/link"]);
    expect(linkUses("import NextLink from 'next/link';")).toEqual(["next/link"]);
    expect(linkUses('const L = await import("next/link");')).toEqual(["next/link"]);
    expect(linkUses("// a next/link <Link> waits on the server")).toEqual([]);
    expect(linkUses('import { pushView } from "./history";')).toEqual([]);
  });
});

describe("no CRM file changes the view through the Next router", () => {
  const files = crmFiles(CRM_DIR);

  it("finds the files it scans", () => {
    expect(files.length).toBeGreaterThan(20);
    expect(files.some((f) => f.endsWith("page.tsx"))).toBe(true);
  });

  it("finds no useRouter, router.push or router.replace", () => {
    const offenders = files
      .map((f) => ({ file: relative(CRM_DIR, f), uses: routerUses(readFileSync(f, "utf-8")) }))
      .filter((o) => o.uses.length > 0);
    expect(offenders).toEqual([]);
  });

  it("finds no next/link Link, which waits on the server as router.push does", () => {
    // CRM-C1 (crm_platform.md §20.4 item 11): a `<Link>` click is a router
    // push. A link in the CRM is an `<a>` whose click calls `pushView`.
    // This file names `next/link` in its fixtures, so it skips itself.
    const offenders = files
      .filter((f) => f !== __filename)
      .map((f) => ({ file: relative(CRM_DIR, f), uses: linkUses(readFileSync(f, "utf-8")) }))
      .filter((o) => o.uses.length > 0);
    expect(offenders).toEqual([]);
  });

  it("finds no in-app <a href> whose plain click skips pushView", () => {
    const offenders = files
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => ({ file: relative(CRM_DIR, f), links: bareInternalLinks(readFileSync(f, "utf-8")) }))
      .filter((o) => o.links.length > 0);
    expect(offenders).toEqual([]);
    // The scan reads the links that exist: the setup link and the record links.
    const banner = readFileSync(join(CRM_DIR, "components", "MirrorBanner.tsx"), "utf-8");
    expect(anchorTags(banner).length).toBeGreaterThan(0);
  });

  it("sends every page view change through pushView", () => {
    const src = readFileSync(join(CRM_DIR, "page.tsx"), "utf-8");
    expect(src).toMatch(/const go = useCallback\(\(next: CrmView\) => pushView\(viewHref\(next\)\)/);
  });
});
