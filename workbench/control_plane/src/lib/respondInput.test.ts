/**
 * An answer from a tab whose account is no longer the signed-in one never
 * reaches the other account's org (WS-51 S2, `chat_run_continuity.md` §4 S2).
 *
 * A switch in another tab changes the cookie under this one. The tab still
 * shows a card it drew for account A, and the browser now speaks as account
 * B. The BFF route refuses the answer before the gateway, and the chat says
 * "Switch to A to answer". The route's check can only refuse, never grant:
 * the gateway still checks the room under the session's own tenant.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { otherAccountOf, otherAccountRefusal, sendRespondInputResult } from "./respondInput";

const BODY = { request_id: "a".repeat(32), answer: "APPROVE", was_freeform: false, thread_id: "t1" };

describe("the refusal", () => {
  // Mutation: return null in every case, and this fails.
  it("refuses an answer drawn for another account", async () => {
    const res = otherAccountRefusal("alice@a.test", "bob@b.test");
    expect(res?.status).toBe(409);
    expect(otherAccountOf(await res!.text())).toBe("alice@a.test");
  });

  it("lets the same account through, in any case, and a body with no account", () => {
    expect(otherAccountRefusal("Alice@A.test ", "alice@a.test")).toBeNull();
    expect(otherAccountRefusal(undefined, "alice@a.test")).toBeNull();
    expect(otherAccountRefusal("", "alice@a.test")).toBeNull();
  });
});

describe("the answer POST hears it", () => {
  it("keeps the card (retry) and names the account", async () => {
    const fetchFn = vi.fn(async () =>
      new Response(JSON.stringify({ detail: { error: "answer_in_other_account", account: "alice@a.test" } }), {
        status: 409,
      }),
    );
    const got = await sendRespondInputResult({ ...BODY, as: "alice@a.test" }, fetchFn as unknown as typeof fetch);
    expect(got).toEqual({ outcome: "retry", resend: null, otherAccount: "alice@a.test" });
    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    const sent = JSON.parse(String(init.body)) as { as?: string };
    expect(sent.as).toBe("alice@a.test");
  });

  it("a plain 409 is still a drop", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ detail: "No pending question" }), { status: 409 }));
    expect(await sendRespondInputResult(BODY, fetchFn as unknown as typeof fetch)).toEqual({
      outcome: "drop", resend: null,
    });
  });
});

describe("the route", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  // Mutation: delete the refusal from the route, and this fails.
  it("refuses before the gateway, so the other org never hears the answer", async () => {
    vi.doMock("@/auth", () => ({ auth: async () => ({ user: { email: "bob@b.test" } }) }));
    vi.doMock("@/lib/gateway", () => ({
      GATEWAY_URL: "http://gw.test",
      requireIdentity: async () => ({ email: "bob@b.test", role: "employee" }),
      gatewayHeaders: async () => ({}),
      gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
    }));
    const gateway = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", gateway);
    const { POST } = await import("@/app/api/agent/respond-input/route");
    const req = new Request("http://app.test/api/agent/respond-input", {
      method: "POST",
      body: JSON.stringify({ ...BODY, as: "alice@a.test" }),
    });
    const res = await POST(req as never);
    expect(res.status).toBe(409);
    expect(otherAccountOf(await res.text())).toBe("alice@a.test");
    expect(gateway).not.toHaveBeenCalled();
  });

  it("forwards the answer when the account is the one that drew the card", async () => {
    vi.doMock("@/auth", () => ({ auth: async () => ({ user: { email: "alice@a.test" } }) }));
    vi.doMock("@/lib/gateway", () => ({
      GATEWAY_URL: "http://gw.test",
      requireIdentity: async () => ({ email: "alice@a.test", role: "employee" }),
      gatewayHeaders: async () => ({}),
      gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
    }));
    const gateway = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal("fetch", gateway);
    const { POST } = await import("@/app/api/agent/respond-input/route");
    const req = new Request("http://app.test/api/agent/respond-input", {
      method: "POST",
      body: JSON.stringify({ ...BODY, as: "alice@a.test" }),
    });
    const res = await POST(req as never);
    expect(res.status).toBe(200);
    expect(gateway).toHaveBeenCalledTimes(1);
  });
});
