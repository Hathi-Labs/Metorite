// `/api/shell/manifest` passes the gateway's answer on. A 404 stays a 404,
// because the workbench reads it as "the package host is off".
//
// Mutation: make the non-ok branch answer 200, and the 404 test fails.
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
  return GET(new NextRequest(`http://app.test/api/shell/manifest${query}`));
};

describe("/api/shell/manifest", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the resolved manifest and sends no request input on", async () => {
    const body = { version: "abc", contributions: {} };
    stubGateway(() => new Response(JSON.stringify(body), { status: 200 }));
    const res = await get("?org=other");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    expect(asked[0]).toBe("http://gw.test/shell/manifest");
  });

  it("keeps a 404 from the gateway (the host is off)", async () => {
    stubGateway(() => new Response("{}", { status: 404 }));
    expect((await get()).status).toBe(404);
  });

  it("answers 502 when the gateway cannot be reached", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    expect((await get()).status).toBe(502);
  });
});
