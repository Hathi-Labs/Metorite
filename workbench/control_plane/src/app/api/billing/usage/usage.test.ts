/**
 * D66's two spend reads, and the gate that keeps one of them admin-only. H-134.
 *
 * Built in `members.test.ts`'s idiom: mock `@/auth`, stub the `fetch` both the
 * gateway and the Console are reached through, invoke the real handler by its
 * `@/` specifier.
 *
 * What this pins, and why each one is a criterion rather than a detail:
 *
 *   - **`usage/members` is ADMIN-ONLY, server-side.** The row names a
 *     colleague and what they cost. `settings/billing` refuses a non-admin,
 *     and the page's own comment calls that "a COURTESY, not a security
 *     boundary" — so this gate is the one that matters, and it must refuse
 *     BEFORE the Console is touched.
 *   - **`usage/activity` scopes a non-admin to themselves**, from the resolved
 *     session and never from a query parameter. The upstream docstring says
 *     the workbench fills `member` "from the signed-in session, never from the
 *     browser"; this is where that promise is kept.
 *   - **A failure to RESOLVE access is a refusal**, not a pass and not a 500.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const session = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () => (session.email ? { user: { email: session.email } } : null),
  isAuthEnabled: true,
}));

const CONSOLE_URL = "https://console.invalid";

let calls: string[] = [];

/**
 * @param isAdmin  what `/auth/me` says, or `null` to make the gateway fail.
 */
function stubFetch(isAdmin: boolean | null, consoleBody: unknown = { rows: [] }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      if (url.includes("/auth/me")) {
        if (isAdmin === null) return new Response("nope", { status: 503 });
        return new Response(
          JSON.stringify({ is_admin: isAdmin, capabilities: [] }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(consoleBody), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

const consoleCalls = () => calls.filter((u) => u.startsWith(CONSOLE_URL));

beforeEach(() => {
  calls = [];
  session.email = "priya@fracktal.in";
  process.env.CUSTOMER_CONSOLE_URL = CONSOLE_URL;
  process.env.CUSTOMER_CONSOLE_ORG_KEY = "cc_live_fixture_notarealsecret";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("per-person spend is admin-only", () => {
  it("refuses a non-admin with 403", async () => {
    stubFetch(false);
    const { GET } = await import("@/app/api/billing/usage/members/route");
    const res = await GET();
    expect(res.status).toBe(403);
  });

  it("does NOT touch the Console when it refuses", async () => {
    // 🔴 A 403 issued after the read already happened is a different and worse
    // bug: the colleague names would have crossed the wire.
    stubFetch(false);
    const { GET } = await import("@/app/api/billing/usage/members/route");
    await GET();
    expect(consoleCalls()).toEqual([]);
  });

  it("admits an admin", async () => {
    stubFetch(true);
    const { GET } = await import("@/app/api/billing/usage/members/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(consoleCalls()).toHaveLength(1);
    expect(consoleCalls()[0]).toContain("/my/usage/members");
  });

  it("refuses a signed-out caller with 401, and reads nothing", async () => {
    session.email = null;
    stubFetch(true);
    const { GET } = await import("@/app/api/billing/usage/members/route");
    const res = await GET();
    expect(res.status).toBe(401);
    expect(consoleCalls()).toEqual([]);
  });

  it("refuses when access cannot be RESOLVED, rather than passing", async () => {
    stubFetch(null);
    const { GET } = await import("@/app/api/billing/usage/members/route");
    const res = await GET();
    expect(res.status).toBe(403);
    expect(consoleCalls()).toEqual([]);
  });
});

describe("the activity breakdown scopes itself from the SESSION", () => {
  it("pins a non-admin to their own member address", async () => {
    stubFetch(false);
    const { GET } = await import("@/app/api/billing/usage/activity/route");
    await GET();
    expect(consoleCalls()[0]).toContain(
      `member=${encodeURIComponent("priya@fracktal.in")}`,
    );
  });

  it("does not scope an admin — they read the organization", async () => {
    stubFetch(true);
    const { GET } = await import("@/app/api/billing/usage/activity/route");
    await GET();
    expect(consoleCalls()[0]).not.toContain("member=");
  });

  it("takes no member from the caller — the handler accepts no request", async () => {
    // ⚠️ The strongest form of "never from the browser": the route signature
    // has no `Request`, so there is nothing a caller could put a parameter on.
    const { GET } = await import("@/app/api/billing/usage/activity/route");
    expect(GET.length).toBe(0);
  });

  it("is refused for a signed-out caller", async () => {
    session.email = null;
    stubFetch(false);
    const { GET } = await import("@/app/api/billing/usage/activity/route");
    const res = await GET();
    expect(res.status).toBe(401);
    expect(consoleCalls()).toEqual([]);
  });

  it("503s an unconfigured deployment rather than passing", async () => {
    process.env.CUSTOMER_CONSOLE_ORG_KEY = "";
    stubFetch(true);
    const { GET } = await import("@/app/api/billing/usage/activity/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect(consoleCalls()).toEqual([]);
  });
});

describe("the by-app breakdown scopes itself from the SESSION (usage slice 3)", () => {
  it("pins a non-admin to their own member address", async () => {
    stubFetch(false);
    const { GET } = await import("@/app/api/billing/usage/apps/route");
    await GET();
    expect(consoleCalls()[0]).toContain("/my/usage/apps");
    expect(consoleCalls()[0]).toContain(
      `member=${encodeURIComponent("priya@fracktal.in")}`,
    );
  });

  it("does not scope an admin — they read the organization", async () => {
    stubFetch(true);
    const { GET } = await import("@/app/api/billing/usage/apps/route");
    await GET();
    expect(consoleCalls()[0]).not.toContain("member=");
  });

  it("takes no member from the caller — the handler accepts no request", async () => {
    const { GET } = await import("@/app/api/billing/usage/apps/route");
    expect(GET.length).toBe(0);
  });

  it("is refused for a signed-out caller, and reads nothing", async () => {
    session.email = null;
    stubFetch(true);
    const { GET } = await import("@/app/api/billing/usage/apps/route");
    const res = await GET();
    expect(res.status).toBe(401);
    expect(consoleCalls()).toEqual([]);
  });
});
