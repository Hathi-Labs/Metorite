// The consent BFF routes (WS-55 EA-4a, spec §10.5), through the real handlers.
//
// Rule 1: a request that is not `Sec-Fetch-Site: same-origin` gets 403, and
// so does a request with no such header. The gateway is never called.
// Rule 2: no answer is a 3xx. A gateway 3xx becomes a 502, and the route
// does not follow it.
//
// Mutations, each one red here:
// - drop the `sameOrigin` check in `relay.ts`: the cross-site cases fail;
// - accept `same-site` too: the `same-site` case fails;
// - pass a gateway 3xx through: the 3xx cases fail;
// - drop `redirect: "manual"`: the "never follows" case fails.
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "a@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "relay-test-secret-at-least-32-characters!",
}));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

import { POST as checkPOST } from "./check/route";
import { POST as approvePOST } from "./approve/route";

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

const ROUTES = [
  ["check", checkPOST],
  ["approve", approvePOST],
] as const;

function request(action: string, fetchSite: string | null, body: unknown = { client_id: "x" }) {
  const headers = new Headers({ "content-type": "application/json" });
  if (fetchSite !== null) headers.set("sec-fetch-site", fetchSite);
  return new NextRequest(`https://app.metorite.test/api/agent-access/authorize/${action}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

describe("the consent BFF routes", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    stubGateway(() => new Response(JSON.stringify({ redirect_to: "http://localhost:5/cb?code=c" }), { status: 200 }));
  });

  describe.each(ROUTES)("%s", (action, POST) => {
    it.each([["cross-site"], ["same-site"], ["none"], [""]])(
      "refuses Sec-Fetch-Site %s with 403, before the gateway",
      async (site) => {
        const res = await POST(request(action, site));
        expect(res.status).toBe(403);
        expect(await res.json()).toEqual({ error: "cross_site" });
        expect(asked).toEqual([]);
      },
    );

    it("refuses a request with no Sec-Fetch-Site header with 403", async () => {
      const res = await POST(request(action, null));
      expect(res.status).toBe(403);
      expect(asked).toEqual([]);
    });

    it("forwards a same-origin request to its own gateway route, body as sent", async () => {
      const body = { client_id: "https://c.example/m.json", consent_ticket: "t" };
      const res = await POST(request(action, "same-origin", body));
      expect(res.status).toBe(200);
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(asked).toHaveLength(1);
      expect(asked[0].url).toBe(`http://gw.test/agent-access/authorize/${action}`);
      expect(asked[0].init?.method).toBe("POST");
      expect(asked[0].init?.body).toBe(JSON.stringify(body));
    });

    it("never follows a redirect of the gateway", async () => {
      await POST(request(action, "same-origin"));
      expect(asked[0].init?.redirect).toBe("manual");
    });

    it.each([301, 302, 303, 307, 308])("turns a gateway %i into a 502, never a 3xx", async (status) => {
      stubGateway(
        () => new Response(null, { status, headers: { location: "http://localhost:5/cb?code=stolen" } }),
      );
      const res = await POST(request(action, "same-origin"));
      expect(res.status).toBe(502);
      expect(res.headers.get("location")).toBeNull();
    });

    it("keeps a gateway refusal's status and body, and the Retry-After of a 503", async () => {
      stubGateway(
        () =>
          new Response(JSON.stringify({ detail: "busy" }), {
            status: 503,
            headers: { "retry-after": "2" },
          }),
      );
      const res = await POST(request(action, "same-origin"));
      expect(res.status).toBe(503);
      expect(res.headers.get("retry-after")).toBe("2");
      expect(await res.json()).toEqual({ detail: "busy" });
    });

    it("answers 502 when the gateway cannot be reached", async () => {
      stubGateway(() => {
        throw new TypeError("fetch failed");
      });
      const res = await POST(request(action, "same-origin"));
      expect(res.status).toBe(502);
    });

    it("refuses a body far larger than a request, before the gateway", async () => {
      const res = await POST(request(action, "same-origin", { x: "y".repeat(20_000) }));
      expect(res.status).toBe(413);
      expect(asked).toEqual([]);
    });
  });

  it("no answer of either route is a 3xx, whatever the gateway says", async () => {
    for (const status of [200, 300, 301, 302, 303, 304, 307, 308, 400, 401, 403, 422, 500, 503]) {
      stubGateway(() => new Response(status === 304 ? null : "{}", { status }));
      for (const [action, POST] of ROUTES) {
        const res = await POST(request(action, "same-origin"));
        expect(res.status < 300 || res.status >= 400, `${action} ${status} -> ${res.status}`).toBe(true);
      }
    }
  });
});
