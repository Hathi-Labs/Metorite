// `/api/shell/waiting/count` passes the count on, keeps a 404 as 404, and
// fails LOUD (WS-AP AP-2a). A failure that answered `{ count: 0 }` would
// tell the sidebar that nothing waits.
//
// Mutation: make the catch answer `{ count: 0 }` with a 200, and the second
// test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const asked: string[] = [];

function stubGateway(reply: () => Response) {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return reply();
    }),
  );
}

const get = async (query = "") => {
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://app.test/api/shell/waiting/count${query}`));
};

describe("/api/shell/waiting/count", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the gateway's count unchanged", async () => {
    const body = { count: 4, capped: ["decision"], sources: { decision: "ok" } };
    stubGateway(() => new Response(JSON.stringify(body), { status: 200 }));
    const res = await get();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    expect(asked[0]).toBe("http://gw.test/shell/waiting/count");
  });

  it("answers 502 when the gateway cannot be reached, never a count", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const res = await get();
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("count");
  });

  it("keeps a 404 as 404: the org is off", async () => {
    stubGateway(() => new Response("{}", { status: 404 }));
    expect((await get()).status).toBe(404);
  });

  it("passes no request value on", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    await get("?org=other&email=b@example.com");
    expect(asked[0]).toBe("http://gw.test/shell/waiting/count");
  });
});
