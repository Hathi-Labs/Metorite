// `/api/shell/feed/state` passes the member's card state on, and fails LOUD
// (`my_day_feed.md` §6.8, MD-2a).
//
// A 200 for a write that never reached the gateway would drop a dismiss or
// a snooze in silence, and the card would come back. So an unreachable
// gateway answers 502.
//
// Mutation, run by hand (2026-10-14): make the catch answer `{ written: 0 }`
// with a 200, and the second test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const identity = { value: { email: "a@example.com" } as unknown };

vi.mock("@/lib/gateway", async () => {
  const { NextResponse } = await import("next/server");
  return {
    GATEWAY_URL: "http://gw.test",
    requireIdentity: async () =>
      identity.value ?? NextResponse.json({ error: "Sign in to continue" }, { status: 401 }),
    gatewayHeaders: async (extra: Record<string, string> = {}) => ({
      ...extra,
      "X-User-Email": "a@example.com",
    }),
    gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
  };
});

const asked: { url: string; init?: RequestInit }[] = [];

function stubGateway(reply: () => Response) {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      asked.push({ url: String(input), init });
      return reply();
    }),
  );
}

const BODY = JSON.stringify({
  items: [{ signal_id: "tasks:9b2e", state_fp: "a41f09c2", kind: "overdue", action: "seen", until: null }],
});

const post = async (body: string = BODY) => {
  const { POST } = await import("./route");
  return POST(new NextRequest("http://app.test/api/shell/feed/state", { method: "POST", body }));
};

describe("/api/shell/feed/state", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.value = { email: "a@example.com" };
  });

  it("forwards the body as sent, and the gateway's answer unchanged", async () => {
    stubGateway(() => new Response(JSON.stringify({ written: 1 }), { status: 200 }));
    const res = await post();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ written: 1 });
    expect(asked[0].url).toBe("http://gw.test/shell/feed/state");
    expect(asked[0].init?.method).toBe("POST");
    expect(asked[0].init?.body).toBe(BODY);
  });

  it("answers 502 when the gateway cannot be reached, never a 200", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const res = await post();
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("written");
  });

  it("keeps a validation refusal's status", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: [] }), { status: 422 }));
    expect((await post()).status).toBe(422);
  });

  it("refuses a body past the cap and asks the gateway nothing", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    const res = await post("x".repeat(16_385));
    expect(res.status).toBe(413);
    expect(asked).toEqual([]);
  });

  it("asks the gateway nothing without a session", async () => {
    identity.value = null;
    stubGateway(() => new Response(JSON.stringify({ written: 1 }), { status: 200 }));
    const res = await post();
    expect(res.status).toBe(401);
    expect(asked).toEqual([]);
  });

  it("exports only its handler and its route config", async () => {
    const mod = await import("./route");
    expect(Object.keys(mod).sort()).toEqual(["POST", "dynamic"]);
  });
});
