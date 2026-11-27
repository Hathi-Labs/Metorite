// The three app-request BFF routes (WS-MCP MCP-P1b-1), through the real handlers.
//
// A decision that is not `Sec-Fetch-Site: same-origin` gets 403, and the
// gateway is never called. The body that leaves holds the decision and the
// key only. A bad id is refused before any fetch. No answer is a 3xx, and
// every answer says `no-store`.
//
// Mutations, each one red here:
// - drop the `sameOrigin` check in `relay.ts`: the cross-site cases fail;
// - send the browser's body as it came: the body case fails;
// - drop `refuseBadSegment`: the dot-dot cases fail;
// - pass a gateway 3xx through: the 3xx case fails.
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "a@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "relay-test-secret-at-least-32-characters!",
}));

vi.mock("@/lib/gateway", () => {
  class NoIdentityError extends Error {}
  return {
    GATEWAY_URL: "http://gw.test",
    NoIdentityError,
    requireIdentity: async () => ({ email: "a@example.com" }),
    gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "a@example.com" }),
    gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
  };
});

import { GET as listGET } from "./route";
import { GET as oneGET } from "./[id]/route";
import { POST as decisionPOST } from "./[id]/decision/route";

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

const ID = "a60e8cc6-81e5-4e3a-ac8e-2ca429703b30";
const params = (id: string) => ({ params: Promise.resolve({ id }) });

function post(fetchSite: string | null, body: unknown = { decision: "approve", idempotency_key: "k1" }) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (fetchSite !== null) headers["sec-fetch-site"] = fetchSite;
  return new NextRequest(`http://app.test/api/agent-access/requests/${ID}/decision`, {
    method: "POST",
    headers,
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

beforeEach(() => {
  stubGateway(() => Response.json({ state: "approved" }, { status: 200 }));
});

describe("the decision", () => {
  it.each([["cross-site"], ["same-site"], ["none"], [null]])(
    "refuses Sec-Fetch-Site %s with 403, and never calls the gateway",
    async (site) => {
      const res = await decisionPOST(post(site), params(ID));
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual({ error: "cross_site" });
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(asked).toEqual([]);
    },
  );

  it("sends the decision and the key only, to the gateway route", async () => {
    const res = await decisionPOST(
      post("same-origin", {
        decision: "decline",
        idempotency_key: "k2",
        organization_id: "11111111-1111-1111-1111-111111111111",
        member: "b@example.com",
      }),
      params(ID),
    );
    expect(res.status).toBe(200);
    expect(asked.map((a) => [a.url, a.init?.method])).toEqual([
      [`http://gw.test/agent-access/requests/${ID}/decision`, "POST"],
    ]);
    expect(JSON.parse(String(asked[0].init?.body))).toEqual({ decision: "decline", idempotency_key: "k2" });
    expect(asked[0].init?.redirect).toBe("manual");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it.each([
    [{ decision: "maybe", idempotency_key: "k" }],
    [{ decision: "approve" }],
    ["not json"],
  ])("refuses a bad body %j with 400, before any fetch", async (body) => {
    const res = await decisionPOST(post("same-origin", body), params(ID));
    expect(res.status).toBe(400);
    expect(asked).toEqual([]);
  });

  it("refuses a dot-dot id before any fetch", async () => {
    const res = await decisionPOST(post("same-origin"), params(".."));
    expect(res.status).toBe(400);
    expect(asked).toEqual([]);
  });

  it("passes a 409 back with the gateway's words", async () => {
    const detail = { error: "already_decided", message: "This request already has an answer." };
    stubGateway(() => Response.json({ detail }, { status: 409 }));
    const res = await decisionPOST(post("same-origin"), params(ID));
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ detail });
  });

  it("turns a gateway 3xx into a 502, and never follows it", async () => {
    stubGateway(() => new Response(null, { status: 302, headers: { location: "https://evil.example" } }));
    const res = await decisionPOST(post("same-origin"), params(ID));
    expect(res.status).toBe(502);
  });
});

describe("the two reads", () => {
  it("the list relays the gateway answer as it is", async () => {
    const body = { rows: [{ request_id: ID }] };
    stubGateway(() => Response.json(body, { status: 200 }));
    const res = await listGET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    expect(asked.map((a) => [a.url, a.init?.method])).toEqual([["http://gw.test/agent-access/requests", "GET"]]);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("one read relays a 404, and refuses a dot-dot id before any fetch", async () => {
    stubGateway(() => Response.json({ detail: "Request not found" }, { status: 404 }));
    const res = await oneGET(new NextRequest("http://app.test/x"), params(ID));
    expect(res.status).toBe(404);
    expect(asked.map((a) => a.url)).toEqual([`http://gw.test/agent-access/requests/${ID}`]);
    asked.length = 0;
    expect((await oneGET(new NextRequest("http://app.test/x"), params(".."))).status).toBe(400);
    expect(asked).toEqual([]);
  });
});
