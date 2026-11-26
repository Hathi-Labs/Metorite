// `GET` and `POST /api/chat/topics`: the topics of the chip (WS-CO CO-2b).
//
// The BFF reads the list and makes a private topic. It passes the body and
// the status through, so the gateway is the one judge of a name. Mutation:
// send `include_hidden=true` on the read -> the read test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
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

const LIST = { topics: [{ id: "t-1", name: "Sales", scope: "company", mine: false, hidden: false }], can_manage: false };

describe("/api/chat/topics (CO-2b)", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("GET reads the list that shows, and passes it through", async () => {
    stubGateway(() => new Response(JSON.stringify(LIST), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(LIST);
    expect(asked[0].url).toBe("http://gw.test/chat/topics");
  });

  it("GET keeps the 404 of a dark org", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
    const { GET } = await import("./route");
    expect((await GET()).status).toBe(404);
  });

  it("POST passes the name through, and keeps a 409 with its words", async () => {
    stubGateway(() =>
      new Response(JSON.stringify({ detail: "You already have a topic with this name." }), { status: 409 }));
    const { POST } = await import("./route");
    const req = new NextRequest("http://app.test/api/chat/topics", {
      method: "POST",
      body: '{"name":"Board prep"}',
      headers: { "Content-Type": "application/json" },
    });
    const res = await POST(req);
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ detail: "You already have a topic with this name." });
    expect(asked[0].url).toBe("http://gw.test/chat/topics");
    expect(asked[0].init?.method).toBe("POST");
    expect(asked[0].init?.body).toBe('{"name":"Board prep"}');
  });

  it("answers 503 when the gateway cannot be reached", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    expect((await GET()).status).toBe(503);
  });
});
