import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { pushView, replaceView } from "./history";

/**
 * The fence for `history.ts`: a CRM view change makes no server request.
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

  it("sends every page view change through pushView", () => {
    const src = readFileSync(join(CRM_DIR, "page.tsx"), "utf-8");
    expect(src).toMatch(/const go = useCallback\(\(next: CrmView\) => pushView\(viewHref\(next\)\)/);
  });
});
