/**
 * WS-30 SC-2a — the Next hops for the customer seat WRITE (assign / release).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2a (done-whens 2/3/4,
 * the Next-side half) · `customer_console.md` §6 item (h) · R11.
 *
 * The transport is browser → **Next hop** → gateway → Console. This file fences
 * the first arrow, in the `checkout.test.ts` idiom: mock `@/auth`, stub the
 * gateway `fetch`, invoke the real handler through its `@/` specifier.
 *
 * What it pins:
 *   - R11 (done-when 3): the outbound body is `{member_email, plan_slug, source}`
 *     ONLY — an `org` / `actor_email` / `email` the browser adds is dropped, and
 *     the acting identity rides the session's `X-User-Email`, never the body.
 *   - the seam (done-when 2): the hop forwards the internal bearer + the session
 *     identity via `lib/gateway.ts` and holds NO deployment key. The bearer/secret
 *     sweep lives in `gateway.test.ts`; here it is re-asserted at source for these
 *     two files so a regression is local and named.
 *   - fail-closed (done-when 4): a signed-out caller 401s and reaches nothing.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Each case decides who is signed in; `proxyToGateway` resolves it through
// `currentIdentity()` → `auth()`.
const session = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () => (session.email ? { user: { email: session.email } } : null),
  isAuthEnabled: true,
}));

const GATEWAY_URL = "http://127.0.0.1:8000";

/** Every gateway request the stub saw, in order. */
let calls: { url: string; init?: RequestInit }[] = [];

function stubFetch(status = 200, body: unknown = { assigned: true }) {
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

function headersOf(init?: RequestInit): Record<string, string> {
  return (init?.headers ?? {}) as Record<string, string>;
}

function sentBody(init?: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init?.body ?? "{}"));
}

async function invokeAssign(bodyObj: Record<string, unknown>) {
  const { NextRequest } = await import("next/server");
  const { POST } = await import("@/app/api/billing/seats/assign/route");
  return POST(
    new NextRequest("http://localhost:3001/api/billing/seats/assign", {
      method: "POST",
      body: JSON.stringify(bodyObj),
      headers: { "content-type": "application/json" },
    }),
  );
}

async function invokeRelease(bodyObj: Record<string, unknown>) {
  const { NextRequest } = await import("next/server");
  const { POST } = await import("@/app/api/billing/seats/release/route");
  return POST(
    new NextRequest("http://localhost:3001/api/billing/seats/release", {
      method: "POST",
      body: JSON.stringify(bodyObj),
      headers: { "content-type": "application/json" },
    }),
  );
}

beforeEach(() => {
  calls = [];
  session.email = "admin@customer.example";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("POST /api/billing/seats/assign", () => {
  it("forwards to the gateway with the internal bearer + the session identity", async () => {
    stubFetch(200, { assigned: true, plan_slug: "center-ops" });

    const res = await invokeAssign({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
      source: "alacarte",
    });

    expect(res.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`${GATEWAY_URL}/seats/assign`);
    const h = headersOf(calls[0].init);
    expect(h.Authorization).toMatch(/^Bearer /);
    expect(h["X-User-Email"]).toBe("admin@customer.example");
  });

  it("names only member_email, plan_slug and source — dropping any org/actor the browser sent (R11)", async () => {
    stubFetch();

    await invokeAssign({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
      source: "alacarte",
      // Everything below is an R11 claim the browser must not be able to make.
      actor_email: "ceo@customer.example",
      org: "someone-elses-org",
      org_slug: "someone-elses-org",
      email: "ceo@customer.example",
    });

    expect(sentBody(calls[0].init)).toEqual({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
      source: "alacarte",
    });
  });

  it("relays the gateway's verdict — a 403 stays a 403", async () => {
    stubFetch(403, { detail: "the acting member is not an active admin" });

    const res = await invokeAssign({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
    });

    expect(res.status).toBe(403);
  });

  it("401s a signed-out caller, and reaches the gateway with nothing", async () => {
    session.email = null;
    stubFetch();

    const res = await invokeAssign({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
    });

    expect(res.status).toBe(401);
    expect(calls).toEqual([]);
  });
});

describe("POST /api/billing/seats/release", () => {
  it("forwards to the gateway release arm, naming member_email and plan_slug only", async () => {
    stubFetch(200, { released: true });

    const res = await invokeRelease({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
      source: "alacarte", // dropped — release takes no source
      actor_email: "ceo@customer.example", // dropped — R11
    });

    expect(res.status).toBe(200);
    expect(calls[0].url).toBe(`${GATEWAY_URL}/seats/release`);
    expect(sentBody(calls[0].init)).toEqual({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
    });
    expect(headersOf(calls[0].init)["X-User-Email"]).toBe("admin@customer.example");
  });

  it("401s a signed-out caller, and reaches the gateway with nothing", async () => {
    session.email = null;
    stubFetch();

    const res = await invokeRelease({
      member_email: "target@customer.example",
      plan_slug: "center-ops",
    });

    expect(res.status).toBe(401);
    expect(calls).toEqual([]);
  });
});

describe("the hops hold no deployment key (§6(f), gateway.test.ts:232 in miniature)", () => {
  const assignSrc = readFileSync(
    fileURLToPath(new URL("./assign/route.ts", import.meta.url)),
    "utf-8",
  );
  const releaseSrc = readFileSync(
    fileURLToPath(new URL("./release/route.ts", import.meta.url)),
    "utf-8",
  );

  it.each([
    ["assign", assignSrc],
    ["release", releaseSrc],
  ])("%s never reads the deployment key or mints a bearer of its own", (_name, src) => {
    // The deployment key is gateway-side and must never reach the browser tier.
    expect(src).not.toContain("CUSTOMER_CONSOLE_DEPLOYMENT_KEY");
    expect(src).not.toContain("GATEWAY_INTERNAL_TOKEN");
    // No hand-built Authorization header — the bearer comes from `proxyToGateway`.
    expect(src).not.toMatch(/Authorization:\s*`Bearer/);
  });

  it.each([
    ["assign", assignSrc],
    ["release", releaseSrc],
  ])("%s is force-dynamic (it resolves the signed-in member)", (_name, src) => {
    expect(src).toContain('export const dynamic = "force-dynamic"');
  });
});
