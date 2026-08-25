/**
 * The two enforcement points refuse a misconfigured production deployment.
 *
 * Spec: `customer_console.md` CP-0 · `saas_operations_doctrine.md` §4
 * finding 2 · decision D33.1. Companion to `src/authPosture.test.ts`, which pins
 * the predicates themselves; this file pins what the *callers* do with them.
 *
 * Both readers of `isAuthEnabled` used to grant on false:
 *
 *   - `proxy.ts`      — "If Microsoft credentials are not configured, run wide
 *                        open (dev mode)" → every route served to anyone.
 *   - `lib/gateway.ts` — returned `DEV_IDENTITY`, a real member with a real
 *                        email, to any anonymous caller.
 *
 * That was correct on a laptop and catastrophic on a box that lost its auth env.
 * `isAuthEnabled` now distinguishes the two, and these tests assert the
 * *refusal*: a 503 rather than a redirect, and `null` rather than an identity.
 *
 * The 503 matters more than it looks. Redirecting to `/signin` when no provider
 * exists produces a login page that cannot log anyone in — an infinite loop that
 * reads to an operator, and to a health check, as "auth is working".
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const posture = vi.hoisted(() => ({
  enabled: true,
  configured: true,
  session: null as { user?: { email?: string } } | null,
}));

vi.mock("@/auth", () => ({
  auth: async () => posture.session,
  get isAuthEnabled() {
    return posture.enabled;
  },
  get isAuthConfigured() {
    return posture.configured;
  },
  get isDevBypass() {
    return !posture.enabled;
  },
}));

import { proxy } from "@/proxy";
import { currentIdentity } from "@/lib/gateway";

/**
 * Minimal stand-in for the NextRequest fields `proxy` actually reads.
 *
 * `headers` joined the set with WS-29 MT-1f slice 1: the proxy now reads the
 * `Host` header to recognise a per-tenant workspace hostname. `cc.example.com`
 * is outside the workspace base domain, so every case below stays on the
 * pre-MT-1f path — which is the point: this file is about the auth posture and
 * must keep measuring exactly that.
 */
function request(pathname: string) {
  return {
    nextUrl: new URL(`https://cc.example.com${pathname}`),
    url: `https://cc.example.com${pathname}`,
    headers: new Headers({ host: "cc.example.com" }),
  } as unknown as Parameters<typeof proxy>[0];
}

beforeEach(() => {
  posture.enabled = true;
  posture.configured = true;
  posture.session = null;
});

describe("proxy — enforcing with nothing to enforce with", () => {
  it("503s every route when production has no provider", async () => {
    posture.enabled = true; // enforcing
    posture.configured = false; // ...but unconfigured

    const res = await proxy(request("/projects"));

    expect(res.status).toBe(503);
    // Not a redirect: /signin cannot sign anybody in without a provider.
    expect(res.headers.get("location")).toBeNull();
  });

  it("503s the sign-in page itself rather than looping", async () => {
    posture.enabled = true;
    posture.configured = false;

    expect((await proxy(request("/signin"))).status).toBe(503);
  });

  it("still serves everything on a laptop", async () => {
    posture.enabled = false; // dev bypass
    posture.configured = false;

    expect((await proxy(request("/projects"))).status).toBe(200);
  });

  it("redirects an unauthenticated caller when a provider DOES exist", async () => {
    posture.enabled = true;
    posture.configured = true;
    posture.session = null;

    const res = await proxy(request("/projects"));

    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/signin");
  });

  it("401s an unauthenticated API caller rather than redirecting it", async () => {
    posture.enabled = true;
    posture.configured = true;

    expect((await proxy(request("/api/projects"))).status).toBe(401);
  });
});

describe("currentIdentity — no silent grant", () => {
  it("returns null in production, never DEV_IDENTITY", async () => {
    posture.enabled = true; // enforcing (configured or not)
    posture.session = null;

    // The old gate handed back a real member here whenever no client id was
    // set, so every downstream `requireIdentity()` succeeded as that member.
    expect(await currentIdentity()).toBeNull();
  });

  it("still hands a dev identity to a laptop", async () => {
    posture.enabled = false;
    posture.session = null;

    expect(await currentIdentity()).toEqual({
      email: "dev@fracktal.in",
      role: "employee",
    });
  });

  it("prefers the real session whenever there is one", async () => {
    posture.enabled = true;
    posture.session = { user: { email: "buyer@customer.example" } };

    expect(await currentIdentity()).toEqual({
      email: "buyer@customer.example",
      role: "employee",
    });
  });
});
