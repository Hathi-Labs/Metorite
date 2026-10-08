/**
 * The catch-all proxies cannot be steered outside their gateway prefix.
 *
 * Each one sends the internal Bearer token, so a path that resolves outside
 * `/projects/` reaches any gateway route: `/internal/*`, `/v1/*`, `/admin/*`.
 * Nine proxies had no guard until 2026-10-08.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { refuseUnsafePath, unsafeGatewayPath } from "./gatewayPath";

const ESCAPES = ["..", ".", "%2e%2e", "%2E%2E", ".%2e", "%2e.", "%2e", "", "a/..", "a\\b", "%2f", "%5C", "..%2f"];

describe("unsafeGatewayPath", () => {
  it("lets an ordinary path through", () => {
    expect(unsafeGatewayPath(["tasks", "3f2a-91", "relations"])).toBeNull();
    expect(unsafeGatewayPath(["vjvarada@fracktal.in", "file.v2.pdf", "..."])).toBeNull();
    expect(unsafeGatewayPath(undefined)).toBeNull();
    expect(unsafeGatewayPath([])).toBeNull();
  });

  it.each(ESCAPES)("refuses the segment %j", (seg) => {
    expect(unsafeGatewayPath(["x", seg, "internal", "drain"])).not.toBeNull();
  });

  it("refuses every path that the URL parser resolves outside the prefix", () => {
    // The property, not a list: a path the guard lets through must stay under
    // the prefix once `new URL` (and so `fetch`) resolves it.
    const parts = ["..", ".", "%2e%2e", ".%2E", "%2e", "a", "%2f", "internal", "", "%5c"];
    for (const a of parts) for (const b of parts) for (const c of parts) {
      const segs = [a, b, c];
      const url = new URL(`http://gw:8080/projects/${segs.join("/")}`);
      const escaped = !url.pathname.startsWith("/projects/");
      if (escaped) expect(unsafeGatewayPath(segs), JSON.stringify(segs)).not.toBeNull();
    }
  });
});

describe("refuseUnsafePath", () => {
  it("answers 400 for an escape, and nothing for a good path", async () => {
    const res = refuseUnsafePath(["..", "internal", "drain"]);
    expect(res?.status).toBe(400);
    expect((await res!.json()).detail).toMatch(/Invalid path/);
    expect(refuseUnsafePath(["tasks"])).toBeNull();
  });
});

// ── Every catch-all proxy calls the guard ─────────────────────────────────

const API_DIR = fileURLToPath(new URL("../app/api", import.meta.url));

function catchAllRoutes(dir: string, inCatchAll = false, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      catchAllRoutes(full, inCatchAll || entry.includes("..."), out);
    } else if (entry === "route.ts" && inCatchAll) {
      out.push(full);
    }
  }
  return out;
}

describe("the catch-all sweep", () => {
  const routes = catchAllRoutes(API_DIR)
    .map((path) => ({ rel: path.slice(API_DIR.length), src: readFileSync(path, "utf8") }))
    // A route that joins its segments into an upstream path.
    .filter((r) => /\bpath\b[^\n]*\.join\("\/"\)/.test(r.src));

  it("finds the proxies, so a moved tree cannot pass it empty", () => {
    expect(routes.length).toBeGreaterThanOrEqual(11);
  });

  it.each(routes.map((r) => [r.rel, r.src]))("%s calls the guard", (_rel, src) => {
    expect(src).toMatch(/refuseUnsafePath\(path\)|unsafeGatewayPath\(path\)/);
  });

  it.each(routes.map((r) => [r.rel, r.src]))("%s refuses before it builds the upstream", (_rel, src) => {
    // Every handler that reads `params` checks the path next.
    const reads = src.match(/const \{ path(?: = \[\])? \} = await params;\n/g) ?? [];
    const guarded = src.match(/const \{ path(?: = \[\])? \} = await params;\n\s+const refused = refuseUnsafePath\(path\);\n\s+if \(refused\) return refused;/g) ?? [];
    if (!/unsafeGatewayPath\(path\)/.test(src)) expect(guarded.length).toBe(reads.length);
  });
});
