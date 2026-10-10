// `/api/auth/me/shell` passes the member's layout on, and fails LOUD (NS-7).
//
// An all-null layout means "never asked", and the shell then asks the first
// sign-in question. So an unreachable gateway must be an error, never a 200
// of nulls, or a database fault would ask a member who already answered.
//
// Mutation: make the GET catch answer the empty layout with a 200, and the
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

const layout = { preset: "engineer", answered: "answered", pins: ["/tasks"], cardOrder: null, newOrder: null };

describe("/api/auth/me/shell", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards the gateway's layout unchanged", async () => {
    stubGateway(() => new Response(JSON.stringify(layout), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest("http://app.test/api/auth/me/shell"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(layout);
    expect(asked[0].url).toBe("http://gw.test/auth/me/shell");
  });

  it("answers 502 when the gateway cannot be reached, never an empty layout", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    const res = await GET(new NextRequest("http://app.test/api/auth/me/shell"));
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("answered");
  });

  it("keeps the gateway's 503 as a refusal, not as a layout", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }));
    const { GET } = await import("./route");
    const res = await GET(new NextRequest("http://app.test/api/auth/me/shell"));
    expect(res.status).toBe(503);
  });

  it("forwards a PUT body as sent, and a missing body as a reset", async () => {
    stubGateway(() => new Response(JSON.stringify(layout), { status: 200 }));
    const { PUT } = await import("./route");
    const body = JSON.stringify({ pins: ["/tasks"] });
    const res = await PUT(new NextRequest("http://app.test/api/auth/me/shell", { method: "PUT", body }));
    expect(res.status).toBe(200);
    expect(asked[0].init?.method).toBe("PUT");
    expect(asked[0].init?.body).toBe(body);
    await PUT(new NextRequest("http://app.test/api/auth/me/shell", { method: "PUT" }));
    expect(asked[1].init?.body).toBe("null");
  });

  it("keeps a validation refusal's status", async () => {
    stubGateway(() => new Response(JSON.stringify({ detail: [] }), { status: 422 }));
    const { PUT } = await import("./route");
    const res = await PUT(
      new NextRequest("http://app.test/api/auth/me/shell", { method: "PUT", body: '{"preset":"x"}' }),
    );
    expect(res.status).toBe(422);
  });

  it("refuses a body far larger than a layout, before the gateway", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    const { PUT } = await import("./route");
    const res = await PUT(
      new NextRequest("http://app.test/api/auth/me/shell", { method: "PUT", body: "x".repeat(9_000) }),
    );
    expect(res.status).toBe(413);
    expect(asked).toHaveLength(0);
  });
});
