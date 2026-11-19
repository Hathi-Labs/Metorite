// Task 3826. `POST /api/agent/respond-input` answers a parked card.
//
// The route once ran a second `auth()` check after `requireIdentity()`. On the
// local stack (`scripts/dev_up.sh`) auth is off, `requireIdentity` gives the dev
// member and `auth()` gives no session, so each Approve answered 401 and the
// card was dropped. Now the route takes ONE identity rule, the one of the other
// routes.
//
// Mutations, each one red here:
// - put the `auth()` session check back: the dev case fails (401, no gateway call);
// - let the route skip `requireIdentity`: the signed-out case fails (the gateway is called).
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state: { identity: { email: string } | null } = { identity: null };
const called: { url: string; body: string }[] = [];

vi.mock("@/auth", () => ({
  // No session in either case: the dev bypass has none.
  auth: async () => null,
  isAuthEnabled: false,
}));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () =>
    state.identity ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "dev@example.test" }),
  gatewayFetch: async (url: string, init?: RequestInit) => {
    called.push({ url, body: String(init?.body ?? "") });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  },
}));

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
    state.identity = null;
  });

  it("answers the gateway for the dev member, who has no session", async () => {
    state.identity = { email: "dev@example.test" };
    const res = await post({ request_id: "r1", answer: "APPROVE", was_freeform: false });
    expect(res.status).toBe(200);
    expect(called).toHaveLength(1);
    expect(called[0].url).toBe("http://gw.test/agent/respond-input");
    expect(JSON.parse(called[0].body)).toMatchObject({ request_id: "r1", answer: "APPROVE" });
  });

  it("answers 401 and calls no gateway when nobody is signed in", async () => {
    const res = await post({ request_id: "r1", answer: "APPROVE" });
    expect(res.status).toBe(401);
    expect(called).toHaveLength(0);
  });
});
