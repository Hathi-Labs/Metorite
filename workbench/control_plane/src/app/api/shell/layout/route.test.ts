// `/api/shell/layout` passes the gateway's answer on. A 404 stays a 404,
// because the workbench reads it as "no org default".
//
// Mutation: make the non-ok branch answer 200, and the 404 test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const who = vi.hoisted(() => ({ signedIn: true }));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => {
    if (who.signedIn) return { email: "a@example.com" };
    const { NextResponse } = await import("next/server");
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  },
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

const put = async (body: unknown = { roles: {} }) => {
  const { PUT } = await import("./route");
  return PUT(
    new NextRequest("http://app.test/api/shell/layout", {
      method: "PUT",
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
    }),
  );
};

const get = async (query = "") => {
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://app.test/api/shell/layout${query}`));
};

describe("/api/shell/layout", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    who.signedIn = true;
  });

  it("forwards the layout and sends no request input on", async () => {
    const body = { roles: { member: { preset: null, pins: ["/tasks"] } }, updatedBy: "", updatedAt: "" };
    stubGateway(() => new Response(JSON.stringify(body), { status: 200 }));
    const res = await get("?org=other&organization_id=x");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    expect(asked).toEqual(["http://gw.test/shell/layout"]);
  });

  it("keeps a 404 from the gateway (the host is off)", async () => {
    stubGateway(() => new Response("{}", { status: 404 }));
    expect((await get()).status).toBe(404);
  });

  it("answers 502 when the gateway cannot be reached", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    expect((await get()).status).toBe(502);
  });

  describe("PUT", () => {
    it("answers as the GET does when nobody is signed in, and calls no gateway", async () => {
      stubGateway(() => new Response("{}", { status: 200 }));
      who.signedIn = false;
      const got = await get();
      const res = await put();
      expect(res.status).toBe(got.status);
      expect(await res.json()).toEqual(await got.json());
      expect(asked).toEqual([]);
    });

    it("sends the body on and passes the status and body back", async () => {
      const body = { roles: { member: { preset: null, pins: ["/tasks"] } } };
      let sent: RequestInit | undefined;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
          sent = init;
          return new Response(JSON.stringify({ ...body, updatedBy: "a@example.com" }), { status: 200 });
        }),
      );
      const res = await put(body);
      expect(res.status).toBe(200);
      expect((await res.json()).updatedBy).toBe("a@example.com");
      expect(sent?.method).toBe("PUT");
      expect(JSON.parse(String(sent?.body))).toEqual(body);
    });

    it("passes a 422 with its reason on", async () => {
      stubGateway(() => new Response(JSON.stringify({ detail: "unknown preset: x" }), { status: 422 }));
      const res = await put();
      expect(res.status).toBe(422);
      expect(await res.json()).toEqual({ detail: "unknown preset: x" });
    });

    it("answers 502 when the gateway cannot be reached", async () => {
      stubGateway(() => {
        throw new TypeError("fetch failed");
      });
      expect((await put()).status).toBe(502);
    });
  });
});
