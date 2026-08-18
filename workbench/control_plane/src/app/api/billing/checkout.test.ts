/**
 * SC-4a done-when 8 / B7 clause 4 — the gate on the checkout proxies.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a, the B7 block.
 *
 * ## Why this file RUNS the handlers instead of reading them
 *
 * B7's subject is *"does the refusal happen **before** the money route is
 * hit"*, and a source regex cannot decide that — it can see that a check
 * exists, never that it runs first. A 403 issued after the Customer Console
 * was already called is a different and worse bug than no 403 at all, so the
 * assertion has to be over a real invocation.
 *
 * The pattern is `src/lib/export.test.ts`, named by the ticket: import the real
 * handler through the same `@/` specifier the app uses, `vi.mock("@/auth")`
 * rather than importing it, `vi.stubGlobal("fetch", …)`, and build requests
 * from `NextRequest` out of `next/server`. ⚠️ **Do not fall back to a source
 * regex out of caution about `signin.test.ts`'s recorded import warning** —
 * that warning is about importing `@/auth`, which this pattern mocks.
 *
 * ## The parametrisation is an EXPLICIT LIST, and a directory sweep is banned
 *
 * The house sweep idiom (`routeFiles(API_DIR).filter(…)`, `signin.test.ts:60-69`)
 * pointed at `src/app/api/billing/**` would go RED immediately on
 * `summary/route.ts` — a **known-open board finding**, recorded at the foot of
 * the B7 block: that route is reachable by any signed-in member and returns the
 * org's credit balance, burn and BYOK status while its own header claims
 * otherwise. It is a live gap in merged code (`f1fcca4f`) and is deliberately
 * **not** fixed by a checkout slice. An implementer meeting a red sweep has two
 * moves and both are wrong — fix the shipped read proxy (scope creep, in a
 * checkout PR), or narrow the sweep to make it pass, which is the CP-6 failure
 * mode: a fence quietly re-shaped around the thing it caught.
 *
 * So the list below is explicit, and `summary/route.ts` is excluded **by name**
 * with the finding cited. A named exclusion is visible in a diff and dies when
 * the finding is fixed; a narrowed sweep is invisible and outlives it. The cost
 * is stated rather than hidden: **"a third proxy is covered without anyone
 * remembering" does NOT hold** under an explicit list.
 *
 * ⚠️ **The list is explicit; its COMPLETENESS is swept** *(repair round
 * 2026-08-19, P2)*. An explicit list with nothing checking it is a list that
 * silently stops being complete — a fifth billing route lands, nobody adds an
 * entry, and the gate fence reports green about routes it has never invoked.
 * So `the gated list is complete` below walks `src/app/api/billing/**` for
 * `route.ts`, subtracts the two exclusions **by name and with their reasons**,
 * and asserts the remainder IS the gated set. That buys back the "a third proxy
 * is covered without anyone remembering" property in the only form an explicit
 * list can have it: the new route does not gate itself, but it cannot arrive
 * unnoticed. Red-first evidence: a transient `src/app/api/billing/probe/route.ts`
 * failed with *`expected [ …3 items ] to deeply equal [ …4 items ]`* naming
 * `probe/route.ts`.
 */

import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { redeemRefusal } from "@/app/settings/billing/lib/checkout";

import { resetKeyOrgCache } from "./_console";

// The handlers resolve the session through `currentIdentity()`. Who is signed
// in is this file's subject, so the mock is reassigned per case rather than
// fixed the way `export.test.ts` fixes it.
const session = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () =>
    session.email ? { user: { email: session.email } } : null,
  isAuthEnabled: true,
}));

const CONSOLE_URL = "https://console.invalid";
const GATEWAY_URL = "http://127.0.0.1:8000";

/** The Console's read-only whoami — the route that says whose key this is. */
const WHOAMI = `${CONSOLE_URL}/me`;

/** The organization the deployment's `cc_live_` key belongs to. */
const KEY_ORG = "fracktal";

/** Every request the stub saw, in order. */
let calls: string[] = [];

interface Orgs {
  /** The slug the gateway's `/auth/me` reports for the CALLER. */
  caller?: string | null;
  /** What the Console's whoami answers — or a transport failure. */
  whoami?: Response | "unreachable";
}

/**
 * A `fetch` that answers the gateway's `/auth/me` with the given capabilities
 * and **fails loudly** for anything else it is asked to reach.
 *
 * The Console is not scripted at all in the refusal cases: if a handler ever
 * calls it, the URL lands in `calls` and the assertion below fails. That is the
 * assertion — not the status code.
 *
 * Two organizations are in play and by default they AGREE (`KEY_ORG`): the
 * caller's, resolved from `/auth/me`, and the key's, resolved from the Console's
 * whoami. `orgs` is how a case makes them disagree.
 */
function stubFetch(
  capabilities: string[] | null,
  consoleAnswer?: Response,
  orgs: Orgs = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      if (url.startsWith(`${GATEWAY_URL}/auth/me`)) {
        if (capabilities === null) return new Response("nope", { status: 503 });
        const caller = orgs.caller === undefined ? KEY_ORG : orgs.caller;
        return new Response(
          JSON.stringify({
            capabilities,
            // The shape `admin/me.py:129-133` returns: the CALLER's org, not
            // the deployment's.
            organization: caller ? { id: "org-uuid", slug: caller } : {},
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url === WHOAMI) {
        if (orgs.whoami === "unreachable") throw new TypeError("fetch failed");
        return (
          orgs.whoami ??
          // `main.py:1228-1234`'s shape, verbatim.
          new Response(
            JSON.stringify({
              organization_id: "org-uuid",
              slug: KEY_ORG,
              status: "active",
              credit_balance: "1000.00",
              key_prefix: "cc_live_fixture",
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          )
        );
      }
      if (url.startsWith(CONSOLE_URL)) {
        return (
          consoleAnswer ??
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

/** Every call that reached the Customer Console, whoami included. */
const consoleCalls = () => calls.filter((u) => u.startsWith(CONSOLE_URL));

/**
 * The MONEY routes — every Console call that is not the read-only whoami.
 *
 * The whoami moves nothing and grants nothing (`main.py:1211-1234`, "Read-only"),
 * and the gate has to reach it to learn which organization it is about to spend
 * for. What must never be reached before a refusal is a route that creates or
 * redeems an order, and that is what this filter names.
 */
const moneyCalls = () => consoleCalls().filter((u) => u !== WHOAMI);

type Invoke = () => Promise<Response>;

/**
 * The two routes excluded from the gated set, each BY NAME with its reason.
 *
 * Keys are paths relative to this directory — the same shape the completeness
 * sweep produces, so an exclusion that stops matching a real file shows up as a
 * red sweep rather than as silence.
 */
const EXCLUDED: Record<string, string> = {
  "summary/route.ts":
    "the known-open board finding (B7's foot): reachable by any signed-in " +
    "member today. Fixing it is its own small ticket, and narrowing this " +
    "sweep to hide it would be the CP-6 failure mode.",
  "catalog/route.ts":
    "session-gated on purpose: it returns the price list, identical for every " +
    "customer, so requiring `billing:purchase` would stop a member seeing " +
    "what things cost before being handed the right to buy them.",
};

/**
 * The TWO write proxies B7 gates, plus the order read that carries the same
 * gate (see `orders/[id]/route.ts`'s header for the argument — it exposes an
 * order's state and the prefix of a code somebody was issued, which is not the
 * summary's data).
 *
 * ⚠️ EXCLUDED BY NAME: `src/app/api/billing/summary/route.ts` — the known-open
 * board finding recorded in `subscription_console.md`'s B7 block. It is
 * reachable by any signed-in member today; fixing it is its own small ticket
 * and NOT this slice's, and narrowing this list to hide it would be the CP-6
 * failure mode.
 *
 * ⚠️ ALSO EXCLUDED BY NAME: `src/app/api/billing/catalog/route.ts` — session-
 * gated on purpose. It returns the price list, which is the same for every
 * customer (the Console's own handler binds its caller to `_` to make that
 * structural), so requiring `billing:purchase` would mean a member could not
 * see what things cost before being handed the right to buy them.
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
      const { POST } = await import(
        "@/app/api/billing/orders/[id]/redeem/route"
      );
      return POST(
        new NextRequest(
          "http://localhost:3001/api/billing/orders/order-1/redeem",
          {
            method: "POST",
            body: JSON.stringify({ code: "cc_disc_abc_secret" }),
            headers: { "content-type": "application/json" },
          },
        ),
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
  process.env.CUSTOMER_CONSOLE_URL = CONSOLE_URL;
  process.env.CUSTOMER_CONSOLE_ORG_KEY = "cc_live_fixture_notarealsecret";
  // The key→organization resolution is cached module-level (the key is fixed
  // by process env in production). Each case scripts its own whoami, so the
  // cache has to start empty or case two reads case one's answer.
  resetKeyOrgCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.CUSTOMER_CONSOLE_URL;
  delete process.env.CUSTOMER_CONSOLE_ORG_KEY;
});

describe.each(GATED)("$name", ({ invoke }) => {
  it("401s a signed-out caller, and reaches nothing", async () => {
    session.email = null;
    stubFetch(["billing:purchase"]);

    const res = await invoke();

    expect(res.status).toBe(401);
    // Nothing at all — not even the identity hop, because there is no identity
    // to resolve.
    expect(calls).toEqual([]);
  });

  it("403s a signed-in member WITHOUT the capability, before the money route", async () => {
    stubFetch(["admin:members:read", "workflows:publish"]);

    const res = await invoke();

    expect(res.status).toBe(403);
    // ⚠️ THE assertion in this file. The gate resolves the caller through the
    // gateway's `/auth/me` — so `fetch` IS called once, and asserting "the
    // fetch mock was never called" literally would fence the wrong thing.
    // What must never happen is a call to the Customer Console: a 403 issued
    // after the money route was hit is a different and worse bug.
    expect(consoleCalls()).toEqual([]);
  });

  it("403s when the capability cannot be resolved at all — fails CLOSED", async () => {
    // A gateway that is down is not permission to buy. The read path's habit
    // of degrading to NO_ACCESS and rendering is right for deciding what to
    // draw and wrong for deciding whether to spend.
    stubFetch(null);

    const res = await invoke();

    expect(res.status).toBe(403);
    expect(consoleCalls()).toEqual([]);
  });

  it("lets a HOLDER of the KEY's own organization through", async () => {
    stubFetch(["billing:purchase"]);

    const res = await invoke();

    expect(res.status).toBe(200);
    expect(moneyCalls()).toHaveLength(1);
    expect(moneyCalls()[0]).toContain(CONSOLE_URL);
    // The org binding was actually resolved rather than assumed.
    expect(consoleCalls()).toContain(WHOAMI);
  });

  it("carries the deployment's own organization key, never a gateway token", async () => {
    stubFetch(["billing:purchase"]);
    await invoke();

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const consoleCall = fetchMock.mock.calls.find(
      (c: unknown[]) =>
        String(c[0]).startsWith(CONSOLE_URL) && String(c[0]) !== WHOAMI,
    );
    const headers = (consoleCall?.[1] as RequestInit)?.headers as Record<
      string,
      string
    >;
    expect(headers.Authorization).toBe("Bearer cc_live_fixture_notarealsecret");
    // Attribution only — it refines within the organization the key pinned and
    // can never move the call to another one.
    expect(headers["X-CC-Member"]).toBe("priya@fracktal.in");
  });

  it("refuses without a Console configured — and only AFTER the capability check", async () => {
    // Order matters: a member without the capability must not be able to learn
    // whether this deployment is wired to a Console.
    delete process.env.CUSTOMER_CONSOLE_URL;
    stubFetch(["admin:members:read"]);

    const res = await invoke();
    expect(res.status).toBe(403);

    calls = [];
    stubFetch(["billing:purchase"]);
    const configured = await invoke();
    expect(configured.status).toBe(503);
    expect(consoleCalls()).toEqual([]);
  });
});

// ── The org binding: the caller's organization IS the key's ─────────────────
//
// `org_placement` is N organizations to ONE deployment. The capability gate
// answers *"may this person buy?"* about the CALLER, while the credential
// binds *"whose account is charged"* to the DEPLOYMENT's key — and until this
// round nothing compared the two. Once capability seeding parameterizes (the
// org-provisioning ticket named in B7 clause 2), an admin of org B passes
// `requirePurchaser` and buys INTO org A: seats granted to the key's
// organization, paid attention nowhere. The fence is that the money route is
// never reached when the two disagree.

describe.each(GATED)("$name — the org binding", ({ invoke }) => {
  it("403s when the caller's organization is not the KEY's, before the money route", async () => {
    stubFetch(["billing:purchase"], undefined, { caller: "other-company" });

    const res = await invoke();

    expect(res.status).toBe(403);
    expect(moneyCalls()).toEqual([]);
  });

  it("names the mismatch honestly — the caller's org, never the key's", async () => {
    stubFetch(["billing:purchase"], undefined, { caller: "other-company" });

    const body = (await (await invoke()).json()) as { detail?: string };

    // Honest: it says which organization the refusal is about, so an admin can
    // act on it instead of filing "billing is broken".
    expect(body.detail).toContain("other-company");
    expect(body.detail).toMatch(/organization/i);
    // ⚠️ And it must NOT name the key's organization: which tenant owns this
    // deployment's billing account is a cross-tenant fact the caller has no
    // need for, and a refusal that leaks it is a membership oracle with better
    // manners.
    expect(body.detail).not.toContain(KEY_ORG);
  });

  it("403s when the caller's own organization cannot be resolved", async () => {
    // `admin/me.py:135-140` degrades to `organization: {}` when the tenant
    // lookup fails. That is right for rendering the app and wrong for spending:
    // an unresolvable org is not permission to buy into whichever one the key
    // happens to hold.
    stubFetch(["billing:purchase"], undefined, { caller: null });

    const res = await invoke();

    expect(res.status).toBe(403);
    expect(moneyCalls()).toEqual([]);
  });

  it("refuses when the whoami answers anything but 200 — never a pass", async () => {
    stubFetch(["billing:purchase"], undefined, {
      whoami: new Response(JSON.stringify({ detail: "Invalid API key" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }),
    });

    const res = await invoke();

    expect(res.status).toBe(503);
    expect(moneyCalls()).toEqual([]);
    // The upstream's words are this deployment's problem, not the customer's.
    expect(JSON.stringify(await res.json())).not.toContain("Invalid API key");
  });

  it("refuses when the whoami is unreachable — never a pass", async () => {
    stubFetch(["billing:purchase"], undefined, { whoami: "unreachable" });

    const res = await invoke();

    expect(res.status).toBe(503);
    expect(moneyCalls()).toEqual([]);
  });

  it("does not cache a FAILED resolution — one blip is not a dead deployment", async () => {
    stubFetch(["billing:purchase"], undefined, { whoami: "unreachable" });
    expect((await invoke()).status).toBe(503);

    calls = [];
    stubFetch(["billing:purchase"]);
    const second = await invoke();

    expect(second.status).toBe(200);
    expect(consoleCalls()).toContain(WHOAMI);
  });
});

// ── The relay policy, which the page's refusal copy depends on ──────────────

describe("the refusal partition survives the proxy", () => {
  it("relays a 409 reason verbatim", async () => {
    stubFetch(
      ["billing:purchase"],
      new Response(JSON.stringify({ detail: { reason: "exhausted" } }), {
        status: 409,
        headers: { "content-type": "application/json" },
      }),
    );
    const { NextRequest } = await import("next/server");
    const { POST } = await import("@/app/api/billing/orders/[id]/redeem/route");
    const res = await POST(
      new NextRequest("http://localhost:3001/api/billing/orders/o/redeem", {
        method: "POST",
        body: JSON.stringify({ code: "cc_disc_abc_secret" }),
        headers: { "content-type": "application/json" },
      }),
      { params: Promise.resolve({ id: "o" }) },
    );

    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ detail: { reason: "exhausted" } });
  });

  it("relays the collapsed 404 byte-for-byte, so unknown and wrong-org stay one shape", async () => {
    const body = JSON.stringify({ detail: "no such discount code" });
    stubFetch(
      ["billing:purchase"],
      new Response(body, {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    );
    const { NextRequest } = await import("next/server");
    const { POST } = await import("@/app/api/billing/orders/[id]/redeem/route");
    const res = await POST(
      new NextRequest("http://localhost:3001/api/billing/orders/o/redeem", {
        method: "POST",
        body: JSON.stringify({ code: "cc_disc_abc_secret" }),
        headers: { "content-type": "application/json" },
      }),
      { params: Promise.resolve({ id: "o" }) },
    );

    expect(res.status).toBe(404);
    expect(await res.text()).toBe(body);
  });

  it("does NOT relay a 503 — that body names this deployment's missing variables", async () => {
    stubFetch(
      ["billing:purchase"],
      new Response(
        JSON.stringify({ detail: "CUSTOMER_CONSOLE_RAZORPAY_KEY_ID unset" }),
        { status: 503, headers: { "content-type": "application/json" } },
      ),
    );
    const { NextRequest } = await import("next/server");
    const { POST } = await import("@/app/api/billing/orders/route");
    const res = await POST(
      new NextRequest("http://localhost:3001/api/billing/orders", {
        method: "POST",
        body: JSON.stringify({ lines: [{ plan_slug: "core", quantity: 1 }] }),
        headers: { "content-type": "application/json" },
      }),
    );

    expect(res.status).toBe(503);
    expect(JSON.stringify(await res.json())).not.toContain("RAZORPAY");
  });

  it("never forwards a price the browser named", async () => {
    // `CreateOrderRequest` carries no amount by design (§9.2): every paisa
    // comes from `plan_catalog`. The proxy rebuilds the basket rather than
    // passing the body through, so a page field cannot become a wire field.
    stubFetch(["billing:purchase"]);
    const { NextRequest } = await import("next/server");
    const { POST } = await import("@/app/api/billing/orders/route");
    await POST(
      new NextRequest("http://localhost:3001/api/billing/orders", {
        method: "POST",
        body: JSON.stringify({
          lines: [{ plan_slug: "core", quantity: 2, unit_price_paise: 1 }],
          total_paise: 1,
        }),
        headers: { "content-type": "application/json" },
      }),
    );

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const consoleCall = fetchMock.mock.calls.find(
      (c: unknown[]) =>
        String(c[0]).startsWith(CONSOLE_URL) && String(c[0]) !== WHOAMI,
    );
    const sent = JSON.parse(String((consoleCall?.[1] as RequestInit)?.body));
    expect(sent).toEqual({ lines: [{ plan_slug: "core", quantity: 2 }] });
  });
});

// ── An upstream 401 is the DEPLOYMENT's key, never the customer's session ───

describe("a Console 401 does not become the browser's 401", () => {
  /** The 401 the Console issues for a missing, malformed, unknown or revoked key. */
  const invalidKey = () =>
    new Response(JSON.stringify({ detail: "Invalid API key" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });

  it("relays it as 502, and the page reads it as unavailable — not as sign in", async () => {
    stubFetch(["billing:purchase"], invalidKey());
    const { NextRequest } = await import("next/server");
    const { POST } = await import("@/app/api/billing/orders/route");
    const res = await POST(
      new NextRequest("http://localhost:3001/api/billing/orders", {
        method: "POST",
        body: JSON.stringify({ lines: [{ plan_slug: "core", quantity: 1 }] }),
        headers: { "content-type": "application/json" },
      }),
    );

    // 401 relayed as 401 tells a signed-in purchaser to sign in — for a fault
    // in a credential they do not hold and cannot fix.
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(JSON.stringify(body)).not.toContain("Invalid API key");

    // The other half, and the half that matters: what the surface then SAYS.
    const refusal = redeemRefusal(res.status, body);
    expect(refusal.kind).toBe("unavailable");
    expect(refusal.message).not.toMatch(/sign in/i);
    expect(refusal.message).toBe("Billing is temporarily unavailable.");
  });

  it("keeps the BFF's own 401 meaning what it says", async () => {
    // The signed-out caller still gets 401 and still reads "Sign in to
    // continue" — the two 401s were the problem and telling them apart is the
    // fix, not suppressing both.
    session.email = null;
    stubFetch(["billing:purchase"]);
    const { NextRequest } = await import("next/server");
    const { POST } = await import("@/app/api/billing/orders/route");
    const res = await POST(
      new NextRequest("http://localhost:3001/api/billing/orders", {
        method: "POST",
        body: JSON.stringify({ lines: [{ plan_slug: "core", quantity: 1 }] }),
        headers: { "content-type": "application/json" },
      }),
    );

    expect(res.status).toBe(401);
    expect(redeemRefusal(res.status, await res.json()).kind).toBe(
      "unauthenticated",
    );
  });

  it("agrees with the sibling read proxy's policy, which it mirrors", async () => {
    // Two relays, ONE policy. `summary/route.ts` reached the same conclusion
    // first and carries the argument; this asserts it has not since changed its
    // mind, because a mirror that goes stale is how two hops start contradicting
    // each other about the same upstream fact.
    const { readFileSync } = await import("node:fs");
    const summary = readFileSync(
      fileURLToPath(new URL("./summary/route.ts", import.meta.url)),
      "utf-8",
    );
    expect(summary).toContain("401 || res.status === 403 ? 502");
  });
});

// ── The exclusions, asserted rather than only commented ─────────────────────

describe("the gated list is complete", () => {
  const BILLING_DIR = fileURLToPath(new URL(".", import.meta.url));

  /** Every `route.ts` under this directory, relative and slash-separated. */
  function routeFiles(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) routeFiles(full, out);
      else if (entry === "route.ts") out.push(full);
    }
    return out;
  }

  it("is every billing route minus the two excluded BY NAME", () => {
    const onDisk = routeFiles(BILLING_DIR)
      .map((p) => relative(BILLING_DIR, p).split(sep).join("/"))
      .sort();

    // The exclusions have to still BE routes, or the reasons above are about
    // files that no longer exist and the sweep is quietly wider than it reads.
    for (const excluded of Object.keys(EXCLUDED)) {
      expect(onDisk, `${excluded} is excluded by name but is not a route`).toContain(
        excluded,
      );
    }

    const owed = onDisk.filter((f) => !(f in EXCLUDED));
    expect(GATED.map((g) => g.file).sort()).toEqual(owed);
  });
});

describe("the excluded routes", () => {
  it("names the read proxy's board finding rather than sweeping it up", async () => {
    // The summary read is reachable by any signed-in member. This test does not
    // assert that is CORRECT — it asserts the exclusion is DELIBERATE and
    // visible, so the day the finding is fixed this case goes red and the entry
    // moves into GATED where it belongs.
    stubFetch([]);
    const { GET } = await import("@/app/api/billing/summary/route");
    const res = await GET();

    expect(res.status).toBe(200);
    expect(consoleCalls()).toHaveLength(1);
  });

  it("keeps the catalog read on the session gate", async () => {
    stubFetch([], new Response(JSON.stringify({ plans: [] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    const { GET } = await import("@/app/api/billing/catalog/route");
    const res = await GET();

    expect(res.status).toBe(200);

    session.email = null;
    calls = [];
    stubFetch([]);
    const signedOut = await GET();
    expect(signedOut.status).toBe(401);
    expect(calls).toEqual([]);
  });
});
