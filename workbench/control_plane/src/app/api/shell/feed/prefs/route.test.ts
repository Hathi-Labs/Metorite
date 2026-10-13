// `/api/shell/feed/prefs` passes My Day's preferences on, and fails LOUD (MD-2d).
//
// `{}` means "the member set no preference". So an unreachable gateway must be
// an error, never a 200 of `{}`, or a database fault would read as "no mutes".
//
// Mutation: make the GET catch answer `{}` with a 200, and the second test fails.
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

const URL_ = "http://app.test/api/shell/feed/prefs";
const stored = { mutes: ["block_soon"], corrections: ["Acme mail never needs me"] };

describe("/api/shell/feed/prefs", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the gateway's preferences unchanged", async () => {
    stubGateway(() => new Response(JSON.stringify(stored), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(stored);
    expect(asked[0].url).toBe("http://gw.test/shell/feed/prefs");
  });

  it("answers 502 when the gateway cannot be reached, never an empty object", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "prefs_unavailable" });
  });

  it("keeps the gateway's 503 as a refusal, not as preferences", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(503);
  });

  it("forwards a PUT body as sent, with the session's identity", async () => {
    stubGateway(() => new Response(JSON.stringify(stored), { status: 200 }));
    const { PUT } = await import("./route");
    const body = JSON.stringify({ key: "mutes", value: ["block_soon"] });
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(stored);
    expect(asked[0].url).toBe("http://gw.test/shell/feed/prefs");
    expect(asked[0].init?.method).toBe("PUT");
    expect(asked[0].init?.body).toBe(body);
    expect(asked[0].init?.headers).toMatchObject({ "X-User-Email": "a@example.com" });
  });

  it("keeps a validation refusal's status", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: [] }), { status: 422 }));
    const { PUT } = await import("./route");
    const res = await PUT(
      new NextRequest(URL_, { method: "PUT", body: '{"key":"theme","value":[]}' }),
    );
    expect(res.status).toBe(422);
  });

  it("refuses a body far larger than the largest value, before the gateway", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    const { PUT } = await import("./route");
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body: "x".repeat(9_000) }));
    expect(res.status).toBe(413);
    expect(asked).toHaveLength(0);
  });
});
