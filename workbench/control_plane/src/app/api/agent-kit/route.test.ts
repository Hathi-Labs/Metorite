// `/api/agent-kit` relays the gateway's kit index and fails LOUD (KIT-4b).
//
// A dark gateway answers 404, and the control draws nothing for it. An
// unreachable gateway is a 502, never a 200 with an index.
//
// Mutation: make the catch answer a 200 index, and the 502 test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const asked: { url: string }[] = [];

function stubGateway(reply: () => Response) {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      asked.push({ url: String(input) });
      return reply();
    }),
  );
}

describe("/api/agent-kit", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("relays the index unchanged", async () => {
    stubGateway(() => new Response(JSON.stringify({ kit_version: 1, clients: ["claude"] }), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest("http://app.test/api/agent-kit"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ kit_version: 1, clients: ["claude"] });
    expect(asked[0].url).toBe("http://gw.test/agent-kit");
  });

  it("keeps the 404 of a dark gateway", async () => {
    stubGateway(() => new Response("{}", { status: 404 }));
    const { GET } = await import("./route");
    expect((await GET(new NextRequest("http://app.test/api/agent-kit"))).status).toBe(404);
  });

  it("answers 502 when the gateway cannot be reached", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    const res = await GET(new NextRequest("http://app.test/api/agent-kit"));
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("kit_version");
  });
});
