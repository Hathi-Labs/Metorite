// The four agent-connection BFF routes (WS-55 EA-4b), through the real handlers.
//
// A9, the BFF half: a DELETE that is not `Sec-Fetch-Site: same-origin` gets
// 403, and so does a DELETE with no such header. The gateway is never called.
// A same-origin DELETE reaches the right gateway route, a list needs no origin
// check, no answer is a 3xx, and every answer says `no-store`.
//
// Mutations, each one red here:
// - drop the `sameOrigin` check in `relay.ts`: the cross-site cases fail;
// - accept `same-site` too: the `same-site` case fails;
// - drop `refuseBadSegment`: the dot-dot case fails;
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

import { GET as mineGET } from "./route";
import { DELETE as mineDELETE } from "./[id]/route";
import { GET as orgGET } from "@/app/api/integrations/agent-connections/route";
import { DELETE as orgDELETE } from "@/app/api/integrations/agent-connections/[id]/route";

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

const ID = "11111111-2222-4333-8444-555555555555";

const DELETES = [
  ["member", mineDELETE, "http://gw.test/agent-access/connections/"],
  ["admin", orgDELETE, "http://gw.test/integrations/agent-connections/"],
] as const;

function del(fetchSite: string | null) {
  const headers: Record<string, string> = {};
  if (fetchSite !== null) headers["sec-fetch-site"] = fetchSite;
  return new NextRequest(`http://app.test/api/x/${ID}`, { method: "DELETE", headers });
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

beforeEach(() => {
  stubGateway(() => new Response(null, { status: 204 }));
});

describe.each(DELETES)("the %s DELETE", (_name, handler, gatewayBase) => {
  it.each([["cross-site"], ["same-site"], ["none"], [null]])(
    "refuses Sec-Fetch-Site %s with 403, and never calls the gateway",
    async (site) => {
      const res = await handler(del(site), params(ID));
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual({ error: "cross_site" });
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(asked).toEqual([]);
    },
  );

  it("passes a same-origin DELETE to the gateway route, and its 204 back", async () => {
    const res = await handler(del("same-origin"), params(ID));
    expect(res.status).toBe(204);
    expect(asked.map((a) => [a.url, a.init?.method])).toEqual([[`${gatewayBase}${ID}`, "DELETE"]]);
    expect(asked[0].init?.redirect).toBe("manual");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("passes a 404 back, so the page can drop a row that is gone", async () => {
    stubGateway(() => Response.json({ detail: "No such agent connection." }, { status: 404 }));
    const res = await handler(del("same-origin"), params(ID));
    expect(res.status).toBe(404);
  });

  it("refuses a dot-dot id before any fetch", async () => {
    const res = await handler(del("same-origin"), params(".."));
    expect(res.status).toBe(400);
    expect(asked).toEqual([]);
  });

  it("turns a gateway 3xx into a 502, and never follows it", async () => {
    stubGateway(() => new Response(null, { status: 302, headers: { location: "https://evil.example" } }));
    const res = await handler(del("same-origin"), params(ID));
    expect(res.status).toBe(502);
  });
});

describe("the two lists", () => {
  it.each([
    ["member", mineGET, "http://gw.test/agent-access/connections"],
    ["admin", orgGET, "http://gw.test/integrations/agent-connections"],
  ] as const)("the %s list relays the gateway answer as it is", async (_name, handler, url) => {
    const body = { connections: [{ id: ID }] };
    stubGateway(() => Response.json(body, { status: 200 }));
    const res = await handler();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    expect(asked.map((a) => [a.url, a.init?.method])).toEqual([[url, "GET"]]);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("keeps a gateway 403 a 403", async () => {
    stubGateway(() => Response.json({ detail: "Forbidden" }, { status: 403 }));
    expect((await orgGET()).status).toBe(403);
  });
});
