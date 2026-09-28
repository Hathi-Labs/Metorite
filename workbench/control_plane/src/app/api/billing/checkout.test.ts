/**
 * SC-4a done-when 8 / B7 clause 4 — the gate on the checkout proxies.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a, the B7 block ·
 * `project-docs/HANDOFF.md` H-152 (the checkout moved to the gateway).
 *
 * ## Why this file RUNS the handlers instead of reading them
 *
 * B7's subject is *"does the refusal happen **before** the money route is
 * hit"*, and a source regex cannot decide that — it can see that a check
 * exists, never that it runs first. A 403 issued after the money route was
 * already called is a different and worse bug than no 403 at all, so the
 * assertion has to be over a real invocation.
 *
 * The pattern is `src/lib/export.test.ts`: import the real handler through
 * the `@/` specifier the app uses, `vi.mock("@/auth")`, stub `fetch`, and
 * build requests from `NextRequest`.
 *
 * ## The money route is the GATEWAY now (H-152)
 *
 * Until 2026-09-28 the checkout called the Customer Console from this tier with
 * `CUSTOMER_CONSOLE_ORG_KEY`, a key that names ONE tenant. It now relays
 * through the gateway's `/billing/orders*`, which holds the per-box deployment
 * key; the Console derives the organization from the signed-in member. So the
 * "money route" below is `${GATEWAY_URL}/billing/orders…`, and the org-binding
 * block that compared the caller's org with the key's is gone: there is no
 * key here to disagree with. The gateway checks `billing:purchase` again
 * (`test_billing_proxy_route.py`).
 *
 * ## The list is explicit; its COMPLETENESS is swept
 *
 * `the gated list is complete` walks `src/app/api/billing/**` for `route.ts`,
 * subtracts the exclusions **by name and with their reasons**, and asserts the
 * remainder IS the gated set. A new billing route cannot arrive unnoticed.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { redeemRefusal } from "@/app/settings/billing/lib/checkout";

// Who is signed in is this file's subject, so the mock is reassigned per case.
const session = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () => (session.email ? { user: { email: session.email } } : null),
  isAuthEnabled: true,
}));

const GATEWAY_URL = "http://127.0.0.1:8000";

/** The gateway's checkout routes — the money route this file guards. */
const MONEY_PREFIX = `${GATEWAY_URL}/billing/orders`;

/** Every request the stub saw, in order. */
let calls: { url: string; init?: RequestInit }[] = [];

const urls = () => calls.map((c) => c.url);
const moneyCalls = () => calls.filter((c) => c.url.startsWith(MONEY_PREFIX));

/**
 * A `fetch` that answers the gateway's `/auth/me` with the given capabilities,
 * answers the money route with `moneyAnswer`, and **fails loudly** for anything
 * else — a Console URL in particular, which no billing route may reach now.
 */
function stubFetch(capabilities: string[] | null, moneyAnswer?: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, init });
      if (url.startsWith(`${GATEWAY_URL}/auth/me`)) {
        if (capabilities === null) return new Response("nope", { status: 503 });
        return new Response(JSON.stringify({ capabilities }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.startsWith(MONEY_PREFIX)) {
        return (
          moneyAnswer ??
          new Response(JSON.stringify({ id: "order-1" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
        );
      }
      throw new Error(`unexpected fetch to ${url}`);
    }),
  );
}

type Invoke = () => Promise<Response>;

/**
 * The reason every billing READ shares (H-152): it is not a money route. It
 * relays through `_gateway.ts` to the gateway's `/billing/*`. Fenced by
 * `reads.test.ts` and `test_billing_proxy_route.py`.
 */
const READ_HOP = "NOT a money route (H-152): a gateway-tier READ with no Console key. ";

/** The routes excluded from the gated set, each BY NAME with its reason. */
const EXCLUDED: Record<string, string> = {
  "summary/route.ts": READ_HOP + "The balance, burn and BYOK status of the member's OWN org.",
  "catalog/route.ts":
    READ_HOP +
    "The price list, identical for every customer, so requiring " +
    "`billing:purchase` would stop a member seeing what things cost.",
  "seats/route.ts": READ_HOP + "The caller's own seat counts. It mints nothing and moves no money.",
  "members/route.ts":
    READ_HOP + "The caller's own roster, for the manage-seats panel. It mints nothing.",
  "usage/activity/route.ts":
    READ_HOP + "D66 (a). The GATEWAY scopes a non-admin to their own spend.",
  "usage/apps/route.ts": READ_HOP + "Usage slice 3. Scoped at the gateway as the activity read is.",
  "usage/members/route.ts":
    READ_HOP +
    "D66 (b), ADMIN-ONLY: the gateway refuses a non-admin with 403 before the " +
    "Console is asked. Reading what was spent and spending are two different acts.",
  "seats/assign/route.ts":
    "NOT a checkout route (SC-2a): a gateway-tier seat WRITE proxy to the " +
    "deployment-key `seat_admin` door, gated Console-side on the admin role. " +
    "Fenced by `seats/manage.test.ts` + `test_seat_admin_proxy_route.py`.",
  "seats/release/route.ts":
    "NOT a checkout route (SC-2a): the release twin of `seats/assign`.",
};

/**
 * The TWO write proxies B7 gates, plus the order read that carries the same
 * gate (see `orders/[id]/route.ts`'s header for the argument).
 */
const GATED: { name: string; file: string; invoke: Invoke }[] = [
  {
    name: "POST app/api/billing/orders/route.ts",
    file: "orders/route.ts",
    invoke: async () => {
      const { NextRequest } = await import("next/server");
      const { POST } = await import("@/app/api/billing/orders/route");
      return POST(
        new NextRequest("http://localhost:3001/api/billing/orders", {
          method: "POST",
          body: JSON.stringify({ lines: [{ plan_slug: "core", quantity: 1 }] }),
          headers: { "content-type": "application/json" },
        }),
      );
    },
  },
  {
    name: "POST app/api/billing/orders/[id]/redeem/route.ts",
    file: "orders/[id]/redeem/route.ts",
    invoke: async () => {
      const { NextRequest } = await import("next/server");
      const { POST } = await import("@/app/api/billing/orders/[id]/redeem/route");
      return POST(
        new NextRequest("http://localhost:3001/api/billing/orders/order-1/redeem", {
          method: "POST",
          body: JSON.stringify({ code: "cc_disc_abc_secret" }),
          headers: { "content-type": "application/json" },
        }),
        { params: Promise.resolve({ id: "order-1" }) },
      );
    },
  },
  {
    name: "GET app/api/billing/orders/[id]/route.ts",
    file: "orders/[id]/route.ts",
    invoke: async () => {
      const { GET } = await import("@/app/api/billing/orders/[id]/route");
      return GET(new Request("http://localhost:3001/api/billing/orders/order-1"), {
        params: Promise.resolve({ id: "order-1" }),
      });
    },
  },
];

beforeEach(() => {
  calls = [];
  session.email = "priya@fracktal.in";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each(GATED)("$name", ({ invoke }) => {
  it("401s a signed-out caller, and reaches nothing", async () => {
    session.email = null;
    stubFetch(["billing:purchase"]);

    const res = await invoke();

    expect(res.status).toBe(401);
    expect(calls).toEqual([]);
  });

  it("403s a signed-in member WITHOUT the capability, before the money route", async () => {
    stubFetch(["admin:members:read", "workflows:publish"]);

    const res = await invoke();

    expect(res.status).toBe(403);
    // ⚠️ THE assertion in this file. The gate resolves the caller through the
    // gateway's `/auth/me`, so `fetch` IS called once. What must never happen
    // is a call to the money route.
    expect(moneyCalls()).toEqual([]);
  });

  it("403s when the capability cannot be resolved at all — fails CLOSED", async () => {
    stubFetch(null);

    const res = await invoke();

    expect(res.status).toBe(403);
    expect(moneyCalls()).toEqual([]);
  });

  it("lets a HOLDER through to the gateway's money route, once", async () => {
    stubFetch(["billing:purchase"]);

    const res = await invoke();

    expect(res.status).toBe(200);
    expect(moneyCalls()).toHaveLength(1);
  });

  it("acts as the SESSION member and carries no Console key", async () => {
    stubFetch(["billing:purchase"]);
    await invoke();

    const headers = moneyCalls()[0].init?.headers as Record<string, string>;
    expect(headers["X-User-Email"]).toBe("priya@fracktal.in");
    expect(headers.Authorization).not.toMatch(/cc_(live|depl)_/);
    // Nothing but the gateway was reached: no Console URL at all.
    expect(urls().every((u) => u.startsWith(GATEWAY_URL))).toBe(true);
  });

  it("answers 503 when the gateway's money route cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        calls.push({ url });
        if (url.startsWith(`${GATEWAY_URL}/auth/me`)) {
          return new Response(JSON.stringify({ capabilities: ["billing:purchase"] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        throw new TypeError("fetch failed");
      }),
    );

    const res = await invoke();

    expect(res.status).toBe(503);
    expect(redeemRefusal(res.status, await res.json()).kind).toBe("unavailable");
  });
});

// ── The relay, which the page's refusal copy depends on ────────────────────

async function redeem(code = "cc_disc_abc_secret"): Promise<Response> {
  const { NextRequest } = await import("next/server");
  const { POST } = await import("@/app/api/billing/orders/[id]/redeem/route");
  return POST(
    new NextRequest("http://localhost:3001/api/billing/orders/o/redeem", {
      method: "POST",
      body: JSON.stringify({ code }),
      headers: { "content-type": "application/json" },
    }),
    { params: Promise.resolve({ id: "o" }) },
  );
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

describe("the refusal partition survives the proxy", () => {
  it("relays a 409 reason verbatim", async () => {
    stubFetch(["billing:purchase"], json(409, { detail: { reason: "exhausted" } }));
    const res = await redeem();

    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ detail: { reason: "exhausted" } });
  });

  it("relays the collapsed 404 byte-for-byte, so unknown and wrong-org stay one shape", async () => {
    const body = JSON.stringify({ detail: "no such discount code" });
    stubFetch(
      ["billing:purchase"],
      new Response(body, { status: 404, headers: { "content-type": "application/json" } }),
    );
    const res = await redeem();

    expect(res.status).toBe(404);
    expect(await res.text()).toBe(body);
  });

  it("reads the gateway's 503 as unavailable, never as sign in", async () => {
    // The gateway turns a Console 401 (this box's own key), an outage and a
    // missing capability into 503. The page must not tell a signed-in
    // purchaser to sign in for a fault they cannot see.
    stubFetch(
      ["billing:purchase"],
      json(503, { detail: "Billing is temporarily unavailable." }),
    );
    const res = await redeem();

    expect(res.status).toBe(503);
    const refusal = redeemRefusal(res.status, await res.json());
    expect(refusal.kind).toBe("unavailable");
    expect(refusal.message).not.toMatch(/sign in/i);
  });

  it("keeps the BFF's own 401 meaning what it says", async () => {
    session.email = null;
    stubFetch(["billing:purchase"]);
    const res = await redeem();

    expect(res.status).toBe(401);
    expect(redeemRefusal(res.status, await res.json()).kind).toBe("unauthenticated");
  });

  it("never forwards a price the browser named", async () => {
    // Every paisa comes from `plan_catalog` (§9.2). The proxy rebuilds the
    // basket rather than passing the body through.
    stubFetch(["billing:purchase"]);
    const { NextRequest } = await import("next/server");
    const { POST } = await import("@/app/api/billing/orders/route");
    await POST(
      new NextRequest("http://localhost:3001/api/billing/orders", {
        method: "POST",
        body: JSON.stringify({
          lines: [{ plan_slug: "core", quantity: 2, unit_price_paise: 1 }],
          total_paise: 1,
          org_slug: "someone-else",
        }),
        headers: { "content-type": "application/json" },
      }),
    );

    const sent = JSON.parse(String(moneyCalls()[0].init?.body));
    expect(sent).toEqual({ lines: [{ plan_slug: "core", quantity: 2 }] });
  });

  it("puts the code in the BODY only, never in a URL", async () => {
    stubFetch(["billing:purchase"]);
    await redeem("cc_disc_abc_secret");

    expect(urls().some((u) => u.includes("cc_disc_abc_secret"))).toBe(false);
    expect(JSON.parse(String(moneyCalls()[0].init?.body))).toEqual({
      code: "cc_disc_abc_secret",
    });
  });
});

// ── The exclusions, asserted rather than only commented ─────────────────────

const BILLING_DIR = fileURLToPath(new URL(".", import.meta.url));

/** Every file under this directory, relative and slash-separated. */
function filesUnder(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) filesUnder(full, out);
    else out.push(full);
  }
  return out;
}

describe("the gated list is complete", () => {
  it("is every billing route minus the ones excluded BY NAME", () => {
    const onDisk = filesUnder(BILLING_DIR)
      .filter((p) => p.endsWith(`${sep}route.ts`))
      .map((p) => relative(BILLING_DIR, p).split(sep).join("/"))
      .sort();

    for (const excluded of Object.keys(EXCLUDED)) {
      expect(onDisk, `${excluded} is excluded by name but is not a route`).toContain(excluded);
    }

    const owed = onDisk.filter((f) => !(f in EXCLUDED));
    expect(GATED.map((g) => g.file).sort()).toEqual(owed);
  });

  it("no billing module reads a Console credential or address (H-152)", () => {
    // The organization key is how one tenant's purchases were billed to
    // another's account on a shared box. No file here may read it again, nor
    // the Console's address: every billing call goes through the gateway.
    const offenders = filesUnder(BILLING_DIR)
      .filter((p) => /\.ts$/.test(p) && !/\.test\.ts$/.test(p))
      .filter((p) => {
        const code = readFileSync(p, "utf-8")
          .replace(/\/\*[\s\S]*?\*\//g, "")
          .replace(/^\s*\/\/.*$/gm, "");
        return /CUSTOMER_CONSOLE_|cc_live_|X-CC-Member/.test(code);
      })
      .map((p) => relative(BILLING_DIR, p));
    expect(offenders).toEqual([]);
  });
});

describe("the excluded READ routes go to the gateway, never the Console (H-152)", () => {
  function stubGateway() {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        calls.push({ url });
        if (url.startsWith(`${GATEWAY_URL}/billing/`)) {
          return json(200, { plans: [] });
        }
        throw new Error(`unexpected fetch to ${url}`);
      }),
    );
  }

  const READS: [string, () => Promise<{ GET: () => Promise<Response> }>][] = [
    ["/billing/summary", () => import("@/app/api/billing/summary/route")],
    ["/billing/catalog", () => import("@/app/api/billing/catalog/route")],
    ["/billing/seats", () => import("@/app/api/billing/seats/route")],
  ];

  it.each(READS)("%s reaches the gateway alone, with no query string", async (path, load) => {
    stubGateway();
    const { GET } = await load();
    const res = await GET();

    expect(res.status).toBe(200);
    expect(urls()).toEqual([`${GATEWAY_URL}${path}`]);
  });

  it.each(READS)("%s reaches nothing for a signed-out caller", async (_path, load) => {
    session.email = null;
    stubGateway();
    const { GET } = await load();
    const res = await GET();

    expect(res.status).toBe(401);
    expect(calls).toEqual([]);
  });
});
