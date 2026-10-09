// `/api/shell/needs` passes the feed on, caps `limit`, and fails LOUD.
//
// An empty list here would draw "Nothing needs you right now", which is a
// claim about the member's day. So an unreachable gateway must be an error
// the card can show, never a 200 of nothing (NS-3, `navigation_shell.md` §7.2).
//
// Mutation: make the catch answer `{ items: [] }` with a 200, and the second
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
  return GET(new NextRequest(`http://app.test/api/shell/needs${query}`));
};

describe("/api/shell/needs", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the gateway's feed unchanged", async () => {
    const feed = { count: 1, items: [{ id: "tasks:1" }], sources: { tasks: "ok" } };
    stubGateway(() => new Response(JSON.stringify(feed), { status: 200 }));
    const res = await get("?limit=30");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(feed);
    expect(asked[0]).toBe("http://gw.test/shell/needs?limit=30");
  });

  it("answers 502 when the gateway cannot be reached, never an empty feed", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const res = await get();
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("items");
  });

  it("keeps a refusal's status", async () => {
    stubGateway(() => new Response("{}", { status: 403 }));
    expect((await get()).status).toBe(403);
  });

  it("caps limit, and passes nothing else on", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    await get("?limit=9999&org=other&email=b@example.com");
    expect(asked[0]).toBe("http://gw.test/shell/needs?limit=50");
    await get("?limit=nope");
    expect(asked.at(-1)).toBe("http://gw.test/shell/needs?limit=30");
  });
});
