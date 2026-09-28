/**
 * H-152 — the seven billing READS travel browser → BFF → gateway, never to the
 * Console with an organization key.
 *
 * Spec: `project-docs/HANDOFF.md` H-152 · `customer_console.md` §6 CP-2h
 * (D-SEAT-4) · `user_management_contract.md` R11.
 *
 * Built in `checkout.test.ts`'s idiom: mock `@/auth`, stub `fetch`, invoke the
 * real handler through its `@/` specifier. What it pins:
 *
 *   - **the hop**: each read reaches exactly one URL, the gateway's
 *     `/billing/*`, with the internal bearer and the SESSION's email, and no
 *     query string and no body a caller could use to name a tenant or person;
 *   - **no Console, no key**: nothing here reaches the Console, and no read
 *     file names `CUSTOMER_CONSOLE_ORG_KEY`, a `cc_live_` key or a bearer of its
 *     own. Replaced `usage/usage.test.ts` and `members/members.test.ts`, whose
 *     subject was that key;
 *   - **the gateway's verdict is relayed as itself**: the 403 that keeps the
 *     per-person table admin-only, the 409 for a two-org member, and the 503
 *     for a box that is not configured;
 *   - **fail closed**: signed out is 401 and reaches nothing; an unreachable
 *     gateway is 503, never a crash.
 *
 * The authorization itself (who sees which spend) is the GATEWAY's, from the
 * tenant plane's resolved access, and is fenced in
 * `tests/unit/test_billing_proxy_route.py`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const session = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () => (session.email ? { user: { email: session.email } } : null),
  isAuthEnabled: true,
}));

const GATEWAY_URL = "http://127.0.0.1:8000";

type Load = () => Promise<{ GET: () => Promise<Response> }>;

/** Each read route, the gateway path it must reach, and its file. */
const READS: [string, string, Load][] = [
  ["summary/route.ts", "/billing/summary", () => import("@/app/api/billing/summary/route")],
  ["seats/route.ts", "/billing/seats", () => import("@/app/api/billing/seats/route")],
  ["members/route.ts", "/billing/members", () => import("@/app/api/billing/members/route")],
  ["catalog/route.ts", "/billing/catalog", () => import("@/app/api/billing/catalog/route")],
  [
    "usage/activity/route.ts",
    "/billing/usage/activity",
    () => import("@/app/api/billing/usage/activity/route"),
  ],
  ["usage/apps/route.ts", "/billing/usage/apps", () => import("@/app/api/billing/usage/apps/route")],
  [
    "usage/members/route.ts",
    "/billing/usage/members",
    () => import("@/app/api/billing/usage/members/route"),
  ],
];

let calls: { url: string; init?: RequestInit }[] = [];

function stubGateway(status = 200, body: unknown = { rows: [] }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, init });
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  calls = [];
  session.email = "priya@fracktal.in";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe.each(READS)("%s", (file, path, load) => {
  it("reaches the gateway's read alone, as the SESSION member", async () => {
    stubGateway(200, { ok: true });
    const { GET } = await load();
    const res = await GET();

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(calls.map((c) => c.url)).toEqual([`${GATEWAY_URL}${path}`]);

    const headers = calls[0].init?.headers as Record<string, string>;
    expect(headers["X-User-Email"]).toBe("priya@fracktal.in");
    // The gateway's internal bearer — never a Console key.
    expect(headers.Authorization).not.toMatch(/cc_(live|depl)_/);
    expect(calls[0].init?.body).toBeUndefined();
  });

  it("takes no request at all, so a caller has nothing to name a tenant with", async () => {
    const { GET } = await load();
    expect(GET.length).toBe(0);
  });

  it("401s a signed-out caller and reaches nothing", async () => {
    session.email = null;
    stubGateway();
    const { GET } = await load();
    const res = await GET();

    expect(res.status).toBe(401);
    expect(calls).toEqual([]);
  });

  it.each([403, 409, 503])("relays the gateway's %i as itself", async (status) => {
    stubGateway(status, { detail: "from the gateway" });
    const { GET } = await load();
    const res = await GET();

    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ detail: "from the gateway" });
  });

  it("answers 503 when the gateway cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const { GET } = await load();
    const res = await GET();

    expect(res.status).toBe(503);
    expect((await res.json()).detail).toBe("Billing is temporarily unavailable.");
  });

  it("holds no Console credential and reads no request input", () => {
    const src = readFileSync(fileURLToPath(new URL(`./${file}`, import.meta.url)), "utf-8");
    // Scanned over the CODE, not the header, which explains the old key.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toContain("CUSTOMER_CONSOLE");
    expect(code).not.toContain("cc_live_");
    expect(code).not.toContain("_console");
    expect(code).not.toMatch(/Authorization/);
    expect(code).not.toMatch(/searchParams|req(uest)?\.(json|url|headers|nextUrl)/);
    expect(code).toContain('export const dynamic = "force-dynamic"');
  });
});

describe("the shared hop", () => {
  const SRC = readFileSync(fileURLToPath(new URL("./_gateway.ts", import.meta.url)), "utf-8");
  const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

  it("goes through lib/gateway's one door and holds no key of its own", () => {
    expect(CODE).toContain('from "@/lib/gateway"');
    expect(CODE).toContain("proxyToGateway(");
    expect(CODE).not.toContain("process.env");
    expect(CODE).not.toMatch(/Authorization/);
  });

  it("names exactly the seven read paths, so no route can widen it", () => {
    const paths = [...CODE.matchAll(/"(\/billing\/[a-z/]+)"/g)].map((m) => m[1]).sort();
    expect(paths).toEqual(READS.map(([, p]) => p).sort());
  });
});
