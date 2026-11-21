// The four box-token BFF routes (WS-55 EA-19c), through the real handlers.
//
// Spec: `project-docs/specs/external_agent_access.md` §10.9 and §15 EA-19c.
//
// A check, a mint, a rotation and "Revoke all" that are not
// `Sec-Fetch-Site: same-origin` get 403, and so does one with no such header.
// The gateway is never called then. A same-origin request reaches the right
// gateway route with the fields of its act only. No answer is a 3xx, and
// every answer says `no-store`.
//
// Named mutations of §10.9, each one red here:
// - R2, a mint without `sameOrigin`: drop the check in `relayBox`. The
//   cross-site cases of the mint and the check fail.
// - R7, a rotate without `sameOrigin`: the same check. The cross-site cases
//   of the rotation fail.
// More mutations, each one red here:
// - accept `same-site` too: the `same-site` cases fail;
// - drop the check in `relayRevokeAll`: its cross-site cases fail;
// - send the browser's body as it came: the field cases fail;
// - drop `refuseBadSegment`: the dot-dot case fails;
// - pass a gateway 3xx through: the 3xx case fails;
// - read the body before the declared length: the length cases fail.
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

import { POST as mintPOST } from "./route";
import { POST as checkPOST } from "./check/route";
import { POST as rotatePOST } from "./[id]/rotate/route";
import { POST as revokeAllPOST } from "./revoke-all/route";
import { MAX_BOX_BODY } from "./relay";

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
const TOKEN = `mbt_${"a".repeat(43)}`;

const MINT = {
  name: "metorite-home",
  scopes: ["projects:read", "projects:write"],
  days: 90,
  pinned_projects: [],
  left_out_projects: [],
  include_personal: false,
};

/** A POST as a browser sends it. *length* is the declared `Content-Length`:
 * the real one by default, a given one, or `null` for none. */
function post(fetchSite: string | null, body: unknown = MINT, length?: string | null) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (fetchSite !== null) headers["sec-fetch-site"] = fetchSite;
  const declared = length === undefined ? String(Buffer.byteLength(text)) : length;
  if (declared !== null) headers["content-length"] = declared;
  return new NextRequest("http://app.test/api/agent-access/box-tokens", {
    method: "POST",
    headers,
    body: text,
  });
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

const CASES = [
  ["check", (r: NextRequest) => checkPOST(r), "http://gw.test/agent-access/box-tokens/check"],
  ["mint", (r: NextRequest) => mintPOST(r), "http://gw.test/agent-access/box-tokens"],
  ["rotate", (r: NextRequest) => rotatePOST(r, params(ID)), `http://gw.test/agent-access/box-tokens/${ID}/rotate`],
  ["revoke-all", (r: NextRequest) => revokeAllPOST(r), "http://gw.test/agent-access/box-tokens/revoke-all"],
] as const;

beforeEach(() => {
  stubGateway(() => Response.json({ token: TOKEN }, { status: 200 }));
});

describe.each(CASES)("the %s route", (_name, handler, url) => {
  it.each([["cross-site"], ["same-site"], ["none"], [null]])(
    "refuses Sec-Fetch-Site %s with 403, and never calls the gateway",
    async (site) => {
      const res = await handler(post(site));
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual({ error: "cross_site" });
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(asked).toEqual([]);
    },
  );

  it("passes a same-origin POST to its gateway route, with no-store", async () => {
    const res = await handler(post("same-origin"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ token: TOKEN });
    expect(asked.map((a) => [a.url, a.init?.method])).toEqual([[url, "POST"]]);
    expect(asked[0].init?.redirect).toBe("manual");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("turns a gateway 3xx into a 502, and never follows it", async () => {
    stubGateway(() => new Response(null, { status: 302, headers: { location: "https://evil.example" } }));
    const res = await handler(post("same-origin"));
    expect(res.status).toBe(502);
  });

  it("passes a gateway 404 back, so a flag that is off reads as no route", async () => {
    stubGateway(() => Response.json({ detail: "Not Found" }, { status: 404 }));
    const res = await handler(post("same-origin"));
    expect(res.status).toBe(404);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });
});

describe("the body that leaves", () => {
  it("the mint sends its own fields and the ticket only", async () => {
    await mintPOST(post("same-origin", { ...MINT, ticket: "t", member_email: "x@example.com", organization_id: "o" }));
    expect(JSON.parse(String(asked[0].init?.body))).toEqual({ ...MINT, ticket: "t" });
    expect((asked[0].init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("the check of a rotation sends the connection id, the days and now", async () => {
    await checkPOST(post("same-origin", { connection_id: ID, days: 30, now: true, member_email: "x" }));
    expect(JSON.parse(String(asked[0].init?.body))).toEqual({ connection_id: ID, days: 30, now: true });
  });

  it("the rotation sends days, now and the ticket only", async () => {
    await rotatePOST(post("same-origin", { days: 30, now: false, ticket: "t", name: "n", org: "o" }), params(ID));
    expect(JSON.parse(String(asked[0].init?.body))).toEqual({ days: 30, now: false, ticket: "t" });
  });

  it("revoke-all sends no body", async () => {
    await revokeAllPOST(post("same-origin", { organization_id: "o" }));
    expect(asked[0].init?.body).toBeUndefined();
  });

  it.each([["not json"], ["[1, 2]"], ["null"], ["7"]])("refuses the body %s with 400 before any fetch", async (body) => {
    const res = await mintPOST(post("same-origin", body));
    expect(res.status).toBe(400);
    expect(asked).toEqual([]);
  });

  it("refuses a POST with no Content-Length with 411, and never reads the body", async () => {
    const req = post("same-origin", MINT, null);
    const read = vi.spyOn(req, "text");
    const res = await mintPOST(req);
    expect(res.status).toBe(411);
    expect(read).not.toHaveBeenCalled();
    expect(asked).toEqual([]);
  });

  it.each([[String(MAX_BOX_BODY + 1)], ["99999999999"], ["-1"]])(
    "judges the declared length %s before the read",
    async (length) => {
      const req = post("same-origin", MINT, length);
      const read = vi.spyOn(req, "text");
      const res = await checkPOST(req);
      expect([411, 413]).toContain(res.status);
      expect(read).not.toHaveBeenCalled();
      expect(asked).toEqual([]);
    },
  );

  it("keeps the cap after the read when the declared length is short", async () => {
    const res = await mintPOST(post("same-origin", "x".repeat(MAX_BOX_BODY + 1), "10"));
    expect(res.status).toBe(413);
    expect(asked).toEqual([]);
  });

  it("refuses a dot-dot id before any fetch", async () => {
    const res = await rotatePOST(post("same-origin", { days: 90, ticket: "t" }), params(".."));
    expect(res.status).toBe(400);
    expect(asked).toEqual([]);
  });

  it("keeps the Retry-After of a gateway 503", async () => {
    stubGateway(() => Response.json({ detail: "busy" }, { status: 503, headers: { "retry-after": "2" } }));
    const res = await checkPOST(post("same-origin"));
    expect(res.status).toBe(503);
    expect(res.headers.get("retry-after")).toBe("2");
  });
});
