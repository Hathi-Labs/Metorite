/**
 * The slug vocabulary's fences — and D51's structural guarantee that the
 * browser tier reads NO request hostname at all.
 *
 * This file used to drive MT-1f slice 1's host-parse decision table. D51
 * (2026-08-24) withdrew subdomain workspaces entirely and the parser went with
 * it; what remains fenced here is (a) the reserved-slug vocabulary and shape
 * rule that survive as CP-2c's live gate, and (b) the INVERSION of the old
 * "only proxy.ts reads the host" rule: with the feature gone, NOTHING under
 * `src/` may read a request hostname. A request hostname is request input
 * (R11), and the first reader someone adds is the first step back toward two
 * opinions about which tenant a request belongs to.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

import { RESERVED_LABELS, SLUG_RE } from "./subdomain";

describe("the reserved-label vocabulary (owner ruling B7 · CP-2c 4a)", () => {
  it("holds every label the owner named, and the platform's own hostnames", () => {
    for (const label of [
      "app", "api", "www", "admin", "console", "signin", "signup",
      "mail", "static", "cdn", "status", "help", "docs",
      "operator", "billing", "auth", "login", "assets", "ws", "dev", "staging",
    ]) {
      expect(RESERVED_LABELS).toContain(label);
    }
  });

  it("every reserved label is itself a valid slug shape — the set exists BECAUSE shape alone admits them", () => {
    for (const label of RESERVED_LABELS) {
      expect(SLUG_RE.test(label)).toBe(true);
    }
  });

  it("is sorted and duplicate-free, so the python-side equality pin diffs cleanly", () => {
    const sorted = [...RESERVED_LABELS].sort();
    expect(RESERVED_LABELS).toEqual(sorted);
    expect(new Set(RESERVED_LABELS).size).toBe(RESERVED_LABELS.length);
  });
});

describe("the slug shape (one home; the gateway's _SLUG_RE is the twin)", () => {
  it("accepts ordinary company slugs", () => {
    for (const slug of ["acme", "fracktal-works", "a1", "x", "a".repeat(63)]) {
      expect(SLUG_RE.test(slug)).toBe(true);
    }
  });

  it("refuses uppercase, dots, leading/trailing hyphens and over-length labels", () => {
    for (const slug of [
      "Acme", "a.b", "-acme", "acme-", "", " ", "a".repeat(64), "a_b", "a b",
    ]) {
      expect(SLUG_RE.test(slug)).toBe(false);
    }
  });
});

describe("D51 — no request-host reader exists in the browser tier at all", () => {
  const HERE = dirname(fileURLToPath(import.meta.url));
  const SRC = join(HERE, "..");

  /**
   * The request-host idioms the old sole-reader fence policed. Deliberately
   * NOT `window.location.host` — the browser reading the address bar it is
   * already on decides nothing about tenancy (`app/whatsapp/lib/callAudio.ts`
   * does it legitimately).
   */
  const HOST_READS = [
    /\.get\(\s*["'`]host["'`]\s*\)/i,
    /nextUrl\.host(name)?\b/,
    /["'`]x-forwarded-host["'`]/i,
  ];

  function walk(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p, out);
      else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p);
    }
    return out;
  }

  it("sweeps the whole tier and finds ZERO readers (the feature is withdrawn, not dark)", () => {
    const files = walk(SRC);
    // Non-vacuity: the sweep must actually be looking at a real tree.
    expect(files.length).toBeGreaterThan(80);
    const offenders = files
      .filter((p) => HOST_READS.some((re) => re.test(readFileSync(p, "utf-8"))))
      .map((p) => p.slice(SRC.length));
    expect(offenders).toEqual([]);
  });

  it("the patterns are not vacuous: they match every idiom and ignore window.location", () => {
    expect(HOST_READS.some((re) => re.test('req.headers.get("host")'))).toBe(true);
    expect(HOST_READS.some((re) => re.test("const h = req.nextUrl.hostname;"))).toBe(true);
    expect(HOST_READS.some((re) => re.test("const h = req.nextUrl.host;"))).toBe(true);
    expect(HOST_READS.some((re) => re.test('"x-forwarded-host"'))).toBe(true);
    expect(HOST_READS.some((re) => re.test("`${window.location.host}${x}`"))).toBe(false);
  });
});
