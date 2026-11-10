// `/api/actions/pending` passes the queue on, and fails LOUD (WS-58 MD-3a).
//
// The approvals page reads an empty queue as "nothing waits", and a link to
// one proposal reads it as "that request is no longer waiting". So an
// unreachable gateway must be an error, never a 200 of an empty list.
//
// Mutation: make a failure answer `NextResponse.json([])` again, and the
// failure tests fail.
import { beforeEach, describe, expect, it, vi } from "vitest";

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

const ROW = { id: "0f8fad5b-d9cb-469f-a165-70867728950e", actor: "agent:x", action: "email.send",
  target: "thread:1", authority: "suggest", disposition: "hold", status: "pending" };

describe("/api/actions/pending", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("answers the queue as a bare list", async () => {
    stubGateway(() => new Response(JSON.stringify({ pending: [ROW], count: 1 }), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([ROW]);
    expect(asked[0]).toBe("http://gw.test/actions/pending");
  });

  it("answers an empty queue as an empty list", async () => {
    stubGateway(() => new Response(JSON.stringify({ pending: [], count: 0 }), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([]);
  });

  it("answers 502 when the gateway cannot be reached, never an empty queue", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(502);
    expect(Array.isArray(await res.json())).toBe(false);
  });

  it("answers 502 for a gateway 5xx, never an empty queue", async () => {
    stubGateway(() => new Response("boom", { status: 500 }));
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(502);
    expect(Array.isArray(await res.json())).toBe(false);
  });

  it("keeps a gateway refusal as a refusal, not as a queue", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 }));
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(403);
    expect(Array.isArray(await res.json())).toBe(false);
  });
});
