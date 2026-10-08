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

import { gatewayUrlEscape, refuseUnsafePath, unsafeGatewayPath } from "./gatewayPath";

const ESCAPES = [
  "..", ".", "%2e%2e", "%2E%2E", ".%2e", "%2e.", "%2e", "", "a/..", "a\\b", "%2f", "%5C", "..%2f",
  // The parser removes tab, LF and CR before it parses (diff review, 2026-10-08).
  "\t", ".\t.", "%2e\n%2e", "..\r", "\t..", "a\u0000b",
  // A decoded ? or # would end the path early.
  "?", "#", "a?b",
];

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
    const parts = ["..", ".", "%2e%2e", ".%2E", "%2e", "a", "%2f", "internal", "", "%5c", ".\t.", "\n", "%2e\r%2e"];
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

describe("gatewayUrlEscape", () => {
  it.each([
    "http://gw:8080/chat/sessions/../../v1/chat/completions?/messages",
    "http://gw:8080/projects/.\t./internal/drain",
    // Next decodes `%0a` in a segment, so the URL holds a LITERAL LF.
    "http://gw:8080/projects/%2e\n%2e/internal/drain",
    "http://gw:8080/projects/a\\..\\internal",
    "http://gw:8080/projects/%2E%2e/internal",
    "http://gw:8080/projects/x/.",
  ])("refuses %j", (raw) => {
    expect(gatewayUrlEscape(raw)).not.toBeNull();
  });

  it.each([
    "http://gw:8080/projects/tasks/3f2a-91",
    "http://gw:8080/memory/vjvarada@fracktal.in/file.v2.pdf",
    "http://gw:8080/x/...",
    "http://gw:8080/email/messages?q=a..b&next=/../x",
    "http://gw:8080/notes#../../x",
  ])("lets %j through", (raw) => {
    expect(gatewayUrlEscape(raw)).toBeNull();
  });

  it("refuses every single param that the parser resolves outside its route", () => {
    // The shape of a `[sessionId]` route: one raw param, then a fixed suffix.
    const parts = ["..", ".", "%2e", ".\t.", "?", "#", "v1", "a", "\n", "%2f", "\\"];
    for (const a of parts) for (const b of parts) for (const c of parts) {
      const raw = `http://gw:8080/chat/sessions/${a}/${b}/${c}/messages`;
      if (!new URL(raw).pathname.startsWith("/chat/sessions/")) {
        expect(gatewayUrlEscape(raw), JSON.stringify([a, b, c])).not.toBeNull();
      }
    }
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
    .map((path) => ({ rel: path.slice(API_DIR.length), src: readSource(path) }))
    // A catch-all that joins anything with "/". Wide on purpose: a new proxy
    // that names its segments `segments` still lands here.
    .filter((r) => /\.join\(["'`]\/["'`]\)/.test(r.src));

  it("finds the proxies, so a moved tree cannot pass it empty", () => {
    expect(routes.length).toBeGreaterThanOrEqual(11);
  });

  it.each(routes.map((r) => [r.rel, r.src]))("%s calls the guard", (_rel, src) => {
    expect(src).toMatch(/refuseUnsafePath\(path\)|unsafeGatewayPath\(path\)/);
  });

  it.each(routes.map((r) => [r.rel, r.src]))("%s refuses before it builds the upstream", (_rel, src) => {
    // Every read of `params`, in any spelling, is followed by the guard. A
    // handler that reads them another way fails here, and is then written in
    // the one spelling this test knows.
    if (/unsafeGatewayPath\(path\)/.test(src)) return; // email, whatsapp: in the builder
    const reads = src.match(/await (?:ctx\.|context\.)?params\b/g) ?? [];
    const guarded = src.match(/const \{ path(?: = \[\])? \} = await (?:ctx\.)?params;\n\s+const refused = refuseUnsafePath\(path\);\n\s+if \(refused\) return refused;/g) ?? [];
    expect(reads.length).toBeGreaterThan(0);
    expect(guarded.length).toBe(reads.length);
  });
});

describe("a URL object built from a param", () => {
  // gatewayFetch can check only a STRING. A URL object has resolved its dot
  // segments already, so a route that builds one from a param must guard it.
  const builds = allRoutes(API_DIR)
    .map((path) => ({ rel: path.slice(API_DIR.length), src: readSource(path) }))
    .flatMap((r) =>
      [...r.src.matchAll(/new URL\(`\$\{GATEWAY_URL\}[^`]*\$\{(\w+)\}/g)].map((m) => ({ ...r, param: m[1] })),
    );

  it("finds the one route that builds one today", () => {
    expect(builds.length).toBeGreaterThanOrEqual(3);
  });

  it.each(builds.map((b) => [b.rel, b.param, b.src]))("%s guards %s", (_rel, param, src) => {
    expect(src).toContain(`refuseUnsafePath([${param}])`);
  });
});

/** A route's source with LF line ends. A Windows checkout can hold CRLF. */
function readSource(path: string): string {
  return readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function allRoutes(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) allRoutes(full, out);
    else if (entry === "route.ts") out.push(full);
  }
  return out;
}
