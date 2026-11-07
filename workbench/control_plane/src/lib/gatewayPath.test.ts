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
import { describe, expect, it, vi } from "vitest";

import { gatewayUrlEscape, refuseUnsafePath, unsafeGatewayPath } from "./gatewayPath";

// The shape helpers of the assistant-profile relay never call the gateway.
// The mock keeps next-auth, which the real module loads, out of this suite.
vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw:8080",
  gatewayFetch: vi.fn(),
  gatewayHeaders: vi.fn(),
  requireIdentity: vi.fn(),
}));

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
    // Once decoded, as uvicorn hands the path to Starlette (verifier, 2026-10-08).
    "http://gw:8080/projects/%2E%2E%2Finternal",
    "http://gw:8080/projects/x%2F..%2F..%2Finternal",
    "http://gw:8080/projects/%252e%252e/internal",
    "http://gw:8080/projects/a%5C..%5Cinternal",
    "http://gw:8080/projects/a%0Ab",
  ])("refuses %j", (raw) => {
    expect(gatewayUrlEscape(raw)).not.toBeNull();
  });

  it.each([
    "http://gw:8080/projects/tasks/3f2a-91",
    "http://gw:8080/memory/vjvarada@fracktal.in/file.v2.pdf",
    "http://gw:8080/x/...",
    "http://gw:8080/email/messages?q=a..b&next=/../x",
    "http://gw:8080/notes#../../x",
    // A model id holds a slash. The settings page sends it encoded, and the
    // gateway route takes it as `{model_id:path}`.
    "http://gw:8080/settings/llm/enabled-models/openai%2Fgpt-4o",
    "http://gw:8080/memory/alice%40fracktal.in",
    "http://gw:8080/files/%E2%80%A4%E2%80%A4",
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
    const reads = src.match(/await (?:ctx\.|context\.)?params\b/g) ?? [];
    const guarded = src.match(/const \{ path(?: = \[\])? \} = await (?:ctx\.)?params;\n\s+const refused = refuseUnsafePath\(path\);\n\s+if \(refused\) return refused;/g) ?? [];
    expect(reads.length).toBeGreaterThan(0);
    expect(guarded.length).toBe(reads.length);
  });
});

describe("a URL object built from a param", () => {
  // gatewayFetch can check only a STRING. A URL object has resolved its dot
  // segments already, so a route that builds one from a param must guard it.
  const files = allRoutes(API_DIR)
    .map((path) => ({ rel: path.slice(API_DIR.length), src: readSource(path) }))
    .map((r) => ({
      ...r,
      params: [...r.src.matchAll(/new URL\(`\$\{GATEWAY_URL\}[^`]*\$\{(\w+)\}/g)].map((m) => m[1]),
    }))
    .filter((r) => r.params.length > 0);

  it("finds the one route that builds one today", () => {
    expect(files.map((f) => f.params.length).reduce((a, b) => a + b, 0)).toBeGreaterThanOrEqual(3);
  });

  it.each(files.map((f) => [f.rel, f.params, f.src] as const))(
    "%s guards each build in its own handler",
    (_rel, params, src) => {
      // One guard per build, so no handler can drop its own (verifier: a
      // `toContain` passed with two of three handlers unguarded).
      for (const param of new Set(params)) {
        const builds = params.filter((p) => p === param).length;
        const guards = src.split(`refuseUnsafePath([${param}])`).length - 1;
        expect(guards, param).toBeGreaterThanOrEqual(builds);
      }
    },
  );
});

// ── Every single route param that reaches a gateway path (H-280) ──────────
//
// Next decodes a `[sessionId]` param once, so `%3F`, `%23` and `%2F` arrive as
// `?`, `#` and `/`. Put raw into `/chat/sessions/${sessionId}/participants/x`,
// a `?` cuts the suffix off, and a DELETE of one participant becomes a DELETE
// of the session. A `/` adds a segment. `encodeURIComponent` does not save
// it: uvicorn decodes `%2F` back to `/` before Starlette routes the path.
//
// So the sweep does not ask HOW a route spells its URL. It reads every route
// file under a `[param]` folder. Each read of `params` must be
// `const { a, b } = await params;` (or a bare `await params;` that keeps no
// value), and the next lines must guard EVERY name it read, in one of four
// ways:
//
// 1. `refuseUnsafePath([a, b])`, then `if (refused) return refused;`.
// 2. SHAPED: the route's own regex. The test takes it out of the source and
//    proves that it refuses each mark.
// 3. SEGMENT: `refuseBadSegment(a)`, which the test calls with each mark.
// 4. EXEMPT: a param that is the gateway's `{x:path}` tail on purpose.
//
// A route that never reads `params` passes them whole to a helper (HELPERS),
// which the test calls with each mark. Any other spelling of a read fails.

/** A segment value that, once decoded, moves a request to another route. */
const ROUTE_MOVERS = ["a?b", "a#b", "a/b", "?", "#", "/", "..", "a%2Fb", ""];

/** The routes with their own shape regex, and the name of that regex. */
const SHAPED: Record<string, { param: string; regex: string }> = {
  "/email/oauth/[provider]/authorize/route.ts": { param: "provider", regex: "PROVIDER_SEGMENT" },
  "/email/oauth/[provider]/callback/route.ts": { param: "provider", regex: "PROVIDER_SEGMENT" },
};

/** The routes that guard their one param with `refuseBadSegment` (`^[\w.:@-]+$`). */
const SEGMENT = new Set([
  "/integrations/connections/[id]/disconnect/route.ts",
  "/integrations/mcp/[name]/oauth/start/route.ts",
  "/integrations/mcp/[name]/route.ts",
  "/integrations/mcp/[name]/test/route.ts",
  "/integrations/plugins/[id]/route.ts",
]);

/**
 * The routes whose param is the LAST segment of a gateway `{x:path}` route.
 * A slash is part of the value there, and no suffix follows it to cut off.
 * `tail` is the end of the gateway URL as the source spells it.
 */
const EXEMPT: Record<string, { param: string; tail: string; why: string }> = {
  "/settings/llm/enabled-models/[id]/route.ts": {
    param: "id",
    tail: "/settings/llm/enabled-models/${encodeURIComponent(modelId)}`",
    why: "a model id holds a slash (openai/gpt-4o); the gateway route is {model_id:path}",
  },
};

/** The routes that pass `params` whole to a helper that checks the shape. */
const HELPERS: Record<string, "idSuffix" | "areaSuffix"> = {
  "/auth/me/assistant-profile/aliases/[id]/route.ts": "idSuffix",
  "/auth/me/assistant-profile/facts/[id]/route.ts": "idSuffix",
  "/auth/me/assistant-profile/summaries/[id]/route.ts": "idSuffix",
  "/auth/me/assistant-profile/interview/[area]/route.ts": "areaSuffix",
};

/** Each route file under a `[param]` folder (not a catch-all), with its param names. */
function singleParamRoutes(dir: string, names: string[] = [], out: { path: string; names: string[] }[] = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      // Below a catch-all, the catch-all sweep above owns the route.
      if (entry.includes("...")) continue;
      const m = /^\[(\w+)\]$/.exec(entry);
      singleParamRoutes(full, m ? [...names, m[1]] : names, out);
    } else if (entry === "route.ts" && names.length > 0) {
      out.push({ path: full, names });
    }
  }
  return out;
}

const relOf = (path: string) => path.slice(API_DIR.length).replace(/\\/g, "/");

describe("the single-param sweep (H-280)", () => {
  const routes = singleParamRoutes(API_DIR).map((r) => ({ rel: relOf(r.path), names: r.names, src: readSource(r.path) }));

  it("finds every route under a [param] folder, so a moved tree cannot pass it empty", () => {
    // No count is written here. A second walk, by path text, must find the
    // same files, and the route of the finding must be one of them.
    const byText = allRoutes(API_DIR)
      .map(relOf)
      .filter((rel) => /\/\[\w+\]\//.test(rel) && !rel.includes("..."));
    expect(routes.map((r) => r.rel).sort()).toEqual(byText.sort());
    expect(routes.map((r) => r.rel)).toContain("/chat/sessions/[sessionId]/participants/[subject]/route.ts");
  });

  it("names only files that exist, each in one list", () => {
    const rels = new Set(routes.map((r) => r.rel));
    const listed = [...Object.keys(SHAPED), ...SEGMENT, ...Object.keys(EXEMPT), ...Object.keys(HELPERS)];
    for (const rel of listed) expect(rels.has(rel), rel).toBe(true);
    expect(new Set(listed).size).toBe(listed.length);
  });

  it.each(routes.map((r) => [r.rel, r.src] as const))("%s guards each param it reads", (rel, src) => {
    const reads = src.match(/await (?:ctx\.|context\.)?params\b/g) ?? [];
    if (reads.length === 0) {
      expect(HELPERS[rel], "a route that never reads its params names its helper").toBeDefined();
      expect(src).toContain(`${HELPERS[rel]}(`);
      return;
    }
    expect(HELPERS[rel], "a HELPERS route reads no params").toBeUndefined();
    const bare = src.match(/^\s*await (?:ctx\.)?params;/gm) ?? [];
    const destructs = [...src.matchAll(/const \{ ([\w, ]+) \} = await (?:ctx\.)?params;\n([^\n]*)\n([^\n]*)/g)];
    expect(bare.length + destructs.length, "a read of params in another spelling").toBe(reads.length);

    for (const [, list, next1, next2] of destructs) {
      const names = list.split(", ");
      const one = names.length === 1 ? names[0] : null;
      const a = next1.trim();
      const b = next2.trim();
      if (SHAPED[rel]) {
        const { param, regex } = SHAPED[rel];
        expect(one).toBe(param);
        expect(a).toBe(`if (!${regex}.test(${param})) {`);
        const lit = new RegExp(`const ${regex} = /(.+)/([a-z]*);`).exec(src);
        expect(lit, regex).not.toBeNull();
        const re = new RegExp(lit![1], lit![2]);
        for (const bad of ROUTE_MOVERS) expect(re.test(bad), JSON.stringify(bad)).toBe(false);
      } else if (SEGMENT.has(rel)) {
        expect(one).not.toBeNull();
        expect(a).toBe(`const bad = refuseBadSegment(${one});`);
        expect(b).toBe("if (bad) return bad;");
      } else if (EXEMPT[rel]) {
        expect(one).toBe(EXEMPT[rel].param);
        expect(src).toContain(EXEMPT[rel].tail);
      } else {
        expect(a, rel).toBe(`const refused = refuseUnsafePath([${names.join(", ")}]);`);
        expect(b, rel).toBe("if (refused) return refused;");
      }
    }
  });

  it("refuseBadSegment refuses each mark", async () => {
    const { unsafeSegment } = await import("@/app/api/integrations/safeSegment");
    for (const bad of ROUTE_MOVERS) expect(unsafeSegment(bad), JSON.stringify(bad)).toBe(true);
  });

  it.each(Object.entries(HELPERS))("%s: its helper refuses each mark", async (_rel, helper) => {
    const relay = await import("@/app/api/auth/me/assistant-profile/relay");
    for (const bad of ROUTE_MOVERS) {
      const got =
        helper === "areaSuffix"
          ? await relay.areaSuffix(Promise.resolve({ area: bad }))
          : await relay.idSuffix("facts", Promise.resolve({ id: bad }));
      expect(got, JSON.stringify(bad)).toBeNull();
    }
    // The summaries take a chat id, which may hold a colon. Still no mark.
    for (const bad of ROUTE_MOVERS) {
      expect(await relay.idSuffix("summaries", Promise.resolve({ id: bad })), JSON.stringify(bad)).toBeNull();
    }
  });
});

describe("a decoded mark in a single param, end to end (H-280)", () => {
  // The source sweep above proves the spelling. This proves the effect, on
  // the route of the finding: a DELETE of one participant must not become a
  // DELETE of the session.
  it.each(["s1?", "s1#", "s1/participants/x", ".."])("DELETE with sessionId %j answers 400 and calls no gateway", async (sessionId) => {
    const gateway = await import("@/lib/gateway");
    const fetchMock = vi.mocked(gateway.gatewayFetch);
    fetchMock.mockClear();
    const { DELETE } = await import("@/app/api/chat/sessions/[sessionId]/participants/[subject]/route");
    const { NextRequest } = await import("next/server");
    const res = await DELETE(new NextRequest("http://localhost/api/x", { method: "DELETE" }), {
      params: Promise.resolve({ sessionId, subject: "bob@fracktal.in" }),
    });
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
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
