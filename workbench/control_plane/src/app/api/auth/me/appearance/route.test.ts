// `/api/auth/me/appearance` passes the member's appearance on, and fails LOUD
// (PKG-20).
//
// An all-null record means "no server copy", and the browser then keeps what
// it shows. So an unreachable gateway must be an error, never a 200 of nulls.
//
// Mutation: make the GET catch answer an all-null record with a 200, and the
// second test fails.
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

const record = { mode: "light", density: "compact", accent: "#e11d48", updated_at: "2026-11-07T10:00:00.000Z" };
const URL_ = "http://app.test/api/auth/me/appearance";

describe("/api/auth/me/appearance", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the gateway's record unchanged", async () => {
    stubGateway(() => new Response(JSON.stringify(record), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(record);
    expect(asked[0].url).toBe("http://gw.test/auth/me/appearance");
    expect(asked[0].init?.cache).toBe("no-store");
  });

  it("answers 502 when the gateway cannot be reached, never an all-null record", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("mode");
  });

  it("keeps the gateway's 503 as a refusal, not as a record", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest(URL_));
    expect(res.status).toBe(503);
    expect(await res.json()).not.toHaveProperty("mode");
  });

  it("forwards a PUT body as sent, with the session's headers", async () => {
    stubGateway(() => new Response(JSON.stringify(record), { status: 200 }));
    const { PUT } = await import("./route");
    const body = JSON.stringify({ mode: "light", density: "compact", accent: "#e11d48" });
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body }));
    expect(res.status).toBe(200);
    expect(asked[0].url).toBe("http://gw.test/auth/me/appearance");
    expect(asked[0].init?.method).toBe("PUT");
    expect(asked[0].init?.body).toBe(body);
    expect(asked[0].init?.headers).toMatchObject({ "Content-Type": "application/json" });
  });

  it("keeps a validation refusal's status", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: [] }), { status: 422 }));
    const { PUT } = await import("./route");
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body: '{"mode":"system"}' }));
    expect(res.status).toBe(422);
  });

  it("answers 502 when a PUT cannot reach the gateway", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { PUT } = await import("./route");
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body: '{"mode":"dark"}' }));
    expect(res.status).toBe(502);
  });

  it("refuses a body over 1 KB, before the gateway", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    const { PUT } = await import("./route");
    const res = await PUT(new NextRequest(URL_, { method: "PUT", body: "x".repeat(1_100) }));
    expect(res.status).toBe(413);
    expect(asked).toHaveLength(0);
  });
});
