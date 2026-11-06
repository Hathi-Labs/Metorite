// The four agent-connection BFF routes (WS-55 EA-4b), through the real handlers.
//
// A9, the BFF half: a DELETE that is not `Sec-Fetch-Site: same-origin` gets
// 403, and so does a DELETE with no such header. The gateway is never called.
// A same-origin DELETE reaches the right gateway route, a list needs no origin
// check, no answer is a 3xx, and every answer says `no-store`.
//
// EA-17c-1 (D-EA-13): the PATCH of a grant follows the same rules. It is
// same origin only, and the body that leaves holds the four fields of a
// grant and nothing else.
//
// Mutations, each one red here:
// - drop the `sameOrigin` check in `relay.ts`: the cross-site cases fail,
//   for the DELETE and for the PATCH;
// - accept `same-site` too: the `same-site` case fails;
// - drop `refuseBadSegment`: the dot-dot case fails;
// - pass a gateway 3xx through: the 3xx case fails;
// - send the browser's body as it came: the PATCH body case fails;
// - read the body before the declared length: the two length cases fail.
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
import { DELETE as mineDELETE, PATCH as minePATCH } from "./[id]/route";
import { MAX_CHANGE_BODY } from "./relay";
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

const GRANT = {
  scopes: ["projects:read"],
  pinned_projects: [ID],
  left_out_projects: [],
  include_personal: true,
};

/** A PATCH as a browser sends it. *length* is the declared `Content-Length`:
 * the real one by default, a given one, or `null` for none. */
function patch(fetchSite: string | null, body: unknown = GRANT, length?: string | null) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (fetchSite !== null) headers["sec-fetch-site"] = fetchSite;
  const declared = length === undefined ? String(Buffer.byteLength(text)) : length;
  if (declared !== null) headers["content-length"] = declared;
  return new NextRequest(`http://app.test/api/agent-access/connections/${ID}`, {
    method: "PATCH",
    headers,
    body: text,
  });
}

describe("the member's PATCH of a grant (EA-17c-1)", () => {
  beforeEach(() => {
    stubGateway(() => Response.json({ id: ID, scopes: ["projects:read"] }, { status: 200 }));
  });

  it.each([["cross-site"], ["same-site"], ["none"], [null]])(
    "refuses Sec-Fetch-Site %s with 403, and never calls the gateway",
    async (site) => {
      const res = await minePATCH(patch(site), params(ID));
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual({ error: "cross_site" });
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(asked).toEqual([]);
    },
  );

  it("sends the four fields of the grant only, to the gateway route", async () => {
    const res = await minePATCH(
      patch("same-origin", { ...GRANT, organization_id: "other-org", member_email: "x@example.com" }),
      params(ID),
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ id: ID, scopes: ["projects:read"] });
    expect(asked.map((a) => [a.url, a.init?.method])).toEqual([
      [`http://gw.test/agent-access/connections/${ID}`, "PATCH"],
    ]);
    expect(JSON.parse(String(asked[0].init?.body))).toEqual(GRANT);
    expect((asked[0].init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(asked[0].init?.redirect).toBe("manual");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("leaves a missing field missing, so the gateway refuses it", async () => {
    const { include_personal: _dropped, ...partial } = GRANT;
    await minePATCH(patch("same-origin", partial), params(ID));
    expect(JSON.parse(String(asked[0].init?.body))).toEqual(partial);
  });

  it.each([["not json"], ["[1, 2]"], ["null"], ["7"]])(
    "refuses the body %s with 400 before any fetch",
    async (body) => {
      const res = await minePATCH(patch("same-origin", body), params(ID));
      expect(res.status).toBe(400);
      expect(asked).toEqual([]);
    },
  );

  it("refuses a body over the cap with 413 before any fetch", async () => {
    const res = await minePATCH(patch("same-origin", "x".repeat(MAX_CHANGE_BODY + 1)), params(ID));
    expect(res.status).toBe(413);
    expect(asked).toEqual([]);
  });

  it("refuses a PATCH with no Content-Length with 411, and never reads the body", async () => {
    const req = patch("same-origin", GRANT, null);
    const read = vi.spyOn(req, "text");
    const res = await minePATCH(req, params(ID));
    expect(res.status).toBe(411);
    expect(await res.json()).toEqual({ error: "length_required" });
    expect(read).not.toHaveBeenCalled();
    expect(asked).toEqual([]);
  });

  it.each([[String(MAX_CHANGE_BODY + 1)], ["99999999999"], ["-1"], ["12abc"]])(
    "judges the declared length %s before the read",
    async (length) => {
      const req = patch("same-origin", GRANT, length);
      const read = vi.spyOn(req, "text");
      const res = await minePATCH(req, params(ID));
      expect([411, 413]).toContain(res.status);
      expect(read).not.toHaveBeenCalled();
      expect(asked).toEqual([]);
    },
  );

  it("keeps the cap after the read when the declared length is short", async () => {
    const res = await minePATCH(patch("same-origin", "x".repeat(MAX_CHANGE_BODY + 1), "10"), params(ID));
    expect(res.status).toBe(413);
    expect(asked).toEqual([]);
  });

  it("refuses a dot-dot id before any fetch", async () => {
    const res = await minePATCH(patch("same-origin"), params(".."));
    expect(res.status).toBe(400);
    expect(asked).toEqual([]);
  });

  it("passes a gateway 422 back as it is", async () => {
    const detail = { detail: { error: "scope_above_ceiling", scopes: ["projects:write"] } };
    stubGateway(() => Response.json(detail, { status: 422 }));
    const res = await minePATCH(patch("same-origin"), params(ID));
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual(detail);
  });

  it("turns a gateway 3xx into a 502, and never follows it", async () => {
    stubGateway(() => new Response(null, { status: 302, headers: { location: "https://evil.example" } }));
    const res = await minePATCH(patch("same-origin"), params(ID));
    expect(res.status).toBe(502);
  });
});
