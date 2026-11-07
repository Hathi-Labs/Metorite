// `/api/shell/waiting` passes the list on, sends only `direction=for_you`,
// keeps a 404 as 404, and fails LOUD (WS-AP AP-2a).
//
// An empty list here would draw "Nothing waits on you", which is a claim
// about the member's day. So an unreachable gateway is a 502, and a dark org
// (404) stays a 404 so the page can show its off state.
//
// Mutation: make the catch answer `{ items: [] }` with a 200, and the second
// test fails. Pass `req.nextUrl.search` on, and the fourth test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const asked: string[] = [];
const signals: (AbortSignal | null | undefined)[] = [];

function stubGateway(reply: () => Response) {
  asked.length = 0;
  signals.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      asked.push(String(input));
      signals.push(init?.signal);
      return reply();
    }),
  );
}

const get = async (query = "") => {
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://app.test/api/shell/waiting${query}`));
};

describe("/api/shell/waiting", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the gateway's list unchanged", async () => {
    const list = {
      direction: "for_you",
      items: [{ id: "decision:1", kind: "decision" }],
      sources: { decision: "ok" },
      totals: { decision: 1 },
      capped: [],
    };
    stubGateway(() => new Response(JSON.stringify(list), { status: 200 }));
    const res = await get();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(list);
    expect(asked[0]).toBe("http://gw.test/shell/waiting?direction=for_you");
    expect(signals[0]).toBeInstanceOf(AbortSignal);
  });

  it("answers 502 when the gateway cannot be reached, never an empty list", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const res = await get();
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("items");
  });

  it("keeps a 404 as 404, so the page can show the off state", async () => {
    stubGateway(() => new Response('{"detail":"Not Found"}', { status: 404 }));
    const res = await get();
    expect(res.status).toBe(404);
    expect(await res.json()).not.toHaveProperty("items");
  });

  it("passes no request value on: only direction=for_you", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    await get("?direction=on_others&scope=team&member=b@example.com&org=other");
    expect(asked[0]).toBe("http://gw.test/shell/waiting?direction=for_you");
  });
});
