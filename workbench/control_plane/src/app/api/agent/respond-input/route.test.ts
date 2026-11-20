// Task 3826. `POST /api/agent/respond-input` answers a parked card.
//
// The route once ran a second `auth()` check after `requireIdentity()`. On the
// local stack (`scripts/dev_up.sh`) auth is off, `requireIdentity` gives the dev
// member and `auth()` gives no session, so each Approve answered 401 and the
// card was dropped. Now the route takes ONE identity rule, the one of the other
// routes.
//
// These tests use the REAL `requireIdentity` and `gatewayHeaders` of
// `@/lib/gateway`. Only `@/auth` (the session and the posture) and the network
// fetch are fakes. So the auth-on cases prove the production path itself.
//
// Mutations, each one red here:
// - put the `auth()` session check back: the dev-bypass case fails (401, no gateway call);
// - let the route skip `requireIdentity`: the auth-on, no-session case fails (the gateway is called);
// - make `currentIdentity` give the dev member when auth is on: the auth-on, no-session case fails.
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const session = vi.hoisted(() => ({ value: null as { user?: { email?: string } } | null }));
const authEnabled = vi.hoisted(() => ({ value: true }));
const called = vi.hoisted(() => [] as { url: string; body: string; headers: Record<string, string> }[]);

vi.mock("@/auth", () => ({
  auth: async () => session.value,
  get isAuthEnabled() {
    return authEnabled.value;
  },
}));

vi.mock("@/lib/gatewayFetch", () => ({
  gatewayFetch: async (url: string, init?: RequestInit) => {
    called.push({
      url,
      body: String(init?.body ?? ""),
      headers: (init?.headers ?? {}) as Record<string, string>,
    });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  },
}));

import { GATEWAY_URL } from "@/lib/gateway";
import { POST } from "./route";

function post(body: unknown) {
  return POST(
    new NextRequest("http://localhost/api/agent/respond-input", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "content-type": "application/json" },
    }),
  );
}

describe("respond-input identity", () => {
  beforeEach(() => {
    called.length = 0;
    session.value = null;
    authEnabled.value = true;
  });

  it("dev bypass (auth off, no session): the answer reaches the gateway as the dev member", async () => {
    authEnabled.value = false;
    const res = await post({ request_id: "r1", answer: "APPROVE", was_freeform: false });
    expect(res.status).toBe(200);
    expect(called).toHaveLength(1);
    expect(called[0].url).toBe(`${GATEWAY_URL}/agent/respond-input`);
    expect(JSON.parse(called[0].body)).toMatchObject({ request_id: "r1", answer: "APPROVE" });
    expect(called[0].headers["X-User-Email"]).toBeTruthy();
  });

  it("auth on, no session: 401 and no gateway call", async () => {
    const res = await post({ request_id: "r1", answer: "APPROVE" });
    expect(res.status).toBe(401);
    expect(called).toHaveLength(0);
  });

  it("auth on, a session with no email: 401 and no gateway call", async () => {
    session.value = { user: {} };
    const res = await post({ request_id: "r1", answer: "APPROVE" });
    expect(res.status).toBe(401);
    expect(called).toHaveLength(0);
  });

  it("auth on, signed in: the answer reaches the gateway as that member", async () => {
    session.value = { user: { email: "member@example.test" } };
    const res = await post({ request_id: "r1", answer: "APPROVE" });
    expect(res.status).toBe(200);
    expect(called).toHaveLength(1);
    expect(called[0].headers["X-User-Email"]).toBe("member@example.test");
  });
});
