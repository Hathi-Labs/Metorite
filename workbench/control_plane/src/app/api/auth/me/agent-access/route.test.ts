// `/api/auth/me/agent-access` passes the member's switch on, and fails LOUD (EA-6h).
//
// The card must never draw "off" for a switch the gateway did not answer
// for. So an unreachable gateway is a 502, never a 200 of `false`.
//
// Mutation: make the GET catch answer `{delete_enabled: false}` with a 200,
// and the second test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

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

const URL_ = "http://app.test/api/auth/me/agent-access";

describe("/api/auth/me/agent-access", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the gateway's switch unchanged", async () => {
    stubGateway(() => new Response(JSON.stringify({ delete_enabled: true }), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ delete_enabled: true });
    expect(asked[0].url).toBe("http://gw.test/auth/me/agent-access");
  });

  it("answers 502 when the gateway cannot be reached, never off", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("delete_enabled");
  });

  it("keeps the 404 of the agent flag", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
    const { GET } = await import("./route");
    expect((await GET(new NextRequest(URL_))).status).toBe(404);
  });

  it("forwards a PUT body as sent", async () => {
    stubGateway(() => new Response(JSON.stringify({ delete_enabled: false }), { status: 200 }));
    const { PUT } = await import("./route");
    const body = JSON.stringify({ delete_enabled: false });
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body }));
    expect(res.status).toBe(200);
    expect(asked[0].init?.method).toBe("PUT");
    expect(asked[0].init?.body).toBe(body);
  });

  it("refuses a large body before the gateway", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    const { PUT } = await import("./route");
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body: "x".repeat(300) }));
    expect(res.status).toBe(413);
    expect(asked).toHaveLength(0);
  });
});
