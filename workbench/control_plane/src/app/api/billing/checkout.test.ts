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
 * remembering" does NOT hold** under an explicit list. It is bought back the
 * day the read proxy is fixed and the sweep can be armed.
 */

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

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

/** Every request the stub saw, in order. */
let calls: string[] = [];

/**
 * A `fetch` that answers the gateway's `/auth/me` with the given capabilities
 * and **fails loudly** for anything else it is asked to reach.
 *
 * The Console is not scripted at all in the refusal cases: if a handler ever
 * calls it, the URL lands in `calls` and the assertion below fails. That is the
 * assertion — not the status code.
 */
function stubFetch(capabilities: string[] | null, consoleAnswer?: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      if (url.startsWith(`${GATEWAY_URL}/auth/me`)) {
        if (capabilities === null) return new Response("nope", { status: 503 });
        return new Response(JSON.stringify({ capabilities }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
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

/** Calls that reached the Customer Console — the money routes. */
const consoleCalls = () => calls.filter((u) => u.startsWith(CONSOLE_URL));

type Invoke = () => Promise<Response>;

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
const GATED: { name: string; invoke: Invoke }[] = [
  {
    name: "POST app/api/billing/orders/route.ts",
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

  it("lets a HOLDER through to the Customer Console", async () => {
    stubFetch(["billing:purchase"]);

    const res = await invoke();

    expect(res.status).toBe(200);
    expect(consoleCalls()).toHaveLength(1);
    expect(consoleCalls()[0]).toContain(CONSOLE_URL);
  });

  it("carries the deployment's own organization key, never a gateway token", async () => {
    stubFetch(["billing:purchase"]);
    await invoke();

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const consoleCall = fetchMock.mock.calls.find((c: unknown[]) =>
      String(c[0]).startsWith(CONSOLE_URL),
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
    const consoleCall = fetchMock.mock.calls.find((c: unknown[]) =>
      String(c[0]).startsWith(CONSOLE_URL),
    );
    const sent = JSON.parse(String((consoleCall?.[1] as RequestInit)?.body));
    expect(sent).toEqual({ lines: [{ plan_slug: "core", quantity: 2 }] });
  });
});

// ── The exclusions, asserted rather than only commented ─────────────────────

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
