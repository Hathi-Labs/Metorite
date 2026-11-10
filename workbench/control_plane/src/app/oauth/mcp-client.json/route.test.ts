// The public CIMD document of the MCP OAuth flow (WS-54 IN-8a, O11).
//
// The gateway builds the document. This route passes a JSON object on, and
// turns every other answer into a 404, so an auth server never reads a page
// that is not the document.
import { beforeEach, describe, expect, it, vi } from "vitest";

const seen = vi.hoisted(() => ({ headers: [] as Record<string, string>[], urls: [] as string[] }));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  serviceHeaders: (reason: string) => ({ Authorization: "Bearer internal", "X-Reason": reason }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const DOC = {
  client_id: "https://app.metorite.com/oauth/mcp-client.json",
  client_name: "Metorite",
  redirect_uris: ["https://app.metorite.com/api/integrations/mcp/oauth/callback"],
};

function stub(res: Response | Error) {
  seen.headers = [];
  seen.urls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      seen.urls.push(String(url));
      seen.headers.push((init?.headers ?? {}) as Record<string, string>);
      if (res instanceof Error) throw res;
      return res;
    }),
  );
}

async function get() {
  const { GET } = await import("./route");
  return GET();
}

describe("GET /oauth/mcp-client.json", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("passes the gateway's document on, as JSON, with the service bearer only", async () => {
    stub(new Response(JSON.stringify(DOC), { status: 200 }));
    const res = await get();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(DOC);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(seen.urls).toEqual(["http://gw.test/integrations/mcp/oauth/client-metadata"]);
    expect(seen.headers[0]["X-User-Email"]).toBeUndefined();
  });

  it("is a 404 when the flag is off at the gateway", async () => {
    stub(new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
    expect((await get()).status).toBe(404);
  });

  it("is a 404 for any answer that is not a JSON object", async () => {
    for (const body of ["<html>oops</html>", "[1,2]", "null"]) {
      stub(new Response(body, { status: 200 }));
      expect((await get()).status, body).toBe(404);
    }
    stub(new Response("{}", { status: 500 }));
    expect((await get()).status).toBe(404);
    stub(new Error("down"));
    expect((await get()).status).toBe(404);
  });
});
