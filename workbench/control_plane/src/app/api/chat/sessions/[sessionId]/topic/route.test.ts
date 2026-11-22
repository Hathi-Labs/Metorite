// `PUT /api/chat/sessions/[sessionId]/topic` moves a chat for the member
// (WS-CO CO-2b, `chat_organization.md` §6).
//
// The BFF passes the body through as it came, so the gateway is the one judge
// of it, and its status passes through. An unsafe id never reaches the
// gateway. Mutation: build a new body from known keys in the route -> the
// pass-through test fails; drop `refuseUnsafePath` -> the unsafe id test fails.
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

function put(id: string, body: string) {
  const req = new NextRequest(`http://app.test/api/chat/sessions/${id}/topic`, {
    method: "PUT",
    body,
    headers: { "Content-Type": "application/json" },
  });
  return { req, ctx: { params: Promise.resolve({ sessionId: id }) } };
}

describe("PUT /api/chat/sessions/[sessionId]/topic (CO-2b)", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes the body and the answer through", async () => {
    stubGateway(() => new Response(JSON.stringify({ ok: true, topicId: "t-1", topicName: "Sales" }), { status: 200 }));
    const { PUT } = await import("./route");
    const { req, ctx } = put("s-1", '{"topic_id":"t-1"}');
    const res = await PUT(req, ctx);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, topicId: "t-1", topicName: "Sales" });
    expect(asked).toHaveLength(1);
    expect(asked[0].url).toBe("http://gw.test/chat/sessions/s-1/topic");
    expect(asked[0].init?.method).toBe("PUT");
    expect(asked[0].init?.body).toBe('{"topic_id":"t-1"}');
  });

  it("passes a body with another key through, so the gateway refuses it", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: [] }), { status: 422 }));
    const { PUT } = await import("./route");
    const { req, ctx } = put("s-1", '{"topic_id":null,"set_by":"auto"}');
    const res = await PUT(req, ctx);
    expect(res.status).toBe(422);
    expect(asked[0].init?.body).toBe('{"topic_id":null,"set_by":"auto"}');
  });

  it("keeps each refusal of the gateway: a dark org and a topic that is not the member's", async () => {
    for (const [status, detail] of [[404, "Not Found"], [404, "Topic not found"], [404, "Session not found"]] as const) {
      stubGateway(() => new Response(JSON.stringify({ detail }), { status }));
      const { PUT } = await import("./route");
      const { req, ctx } = put("s-1", '{"topic_id":null}');
      const res = await PUT(req, ctx);
      expect(res.status).toBe(status);
      expect(await res.json()).toEqual({ detail });
    }
  });

  it("never sends an unsafe id to the gateway", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    const { PUT } = await import("./route");
    const { req, ctx } = put("..", '{"topic_id":null}');
    const res = await PUT(req, ctx);
    expect(res.status).toBeGreaterThanOrEqual(400);
    expect(asked).toHaveLength(0);
  });

  it("answers 503 when the gateway cannot be reached", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { PUT } = await import("./route");
    const { req, ctx } = put("s-1", '{"topic_id":null}');
    expect((await PUT(req, ctx)).status).toBe(503);
  });
});
