// The BFF relays of the Zoho Books connect (API-5): the status, the start and
// the pick. These cases RUN each handler: who may call it, what goes
// upstream, and that the browser goes only to the https URL of the gateway.
import { readFileSync } from "node:fs";
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ signedIn: true }));

// `sameOrigin` is the real one. Its module imports the auth config, so the
// config is a stub here, as in the MCP start test.
vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "dana@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "books-test-secret-at-least-32-characters!",
}));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({ "X-User-Email": "dana@example.com" }),
  requireIdentity: async () =>
    identity.signedIn
      ? { email: "dana@example.com" }
      : NextResponse.json({ error: "Sign in to continue" }, { status: 401 }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const BIND = "http://localhost:3001";
const CONSENT = "https://accounts.zoho.com/oauth/v2/auth?scope=ZohoBooks.invoices.READ&state=zb.s1.g";
const PICK = "AbCdEfGhIjKlMnOpQrStUv";
let calls: { url: string; init?: RequestInit }[] = [];

function stubGateway(res: Response) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return res;
    }),
  );
}

const answer = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function post(path: string, { site = "same-origin", body }: { site?: string | null; body?: string } = {}) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (site) headers["sec-fetch-site"] = site;
  return new NextRequest(`${BIND}${path}`, { method: "POST", headers, body });
}

async function status(query = "") {
  const { GET } = await import("./route");
  return GET(new NextRequest(`${BIND}/api/integrations/api/zoho_books${query}`));
}

async function start(opts: { site?: string | null; body?: string } = {}) {
  const { POST } = await import("./start/route");
  return POST(post("/api/integrations/api/zoho_books/start?org=other", opts));
}

async function pick(body: unknown, site: string | null = "same-origin") {
  const { POST } = await import("./pick/route");
  return POST(post("/api/integrations/api/zoho_books/pick", { site, body: JSON.stringify(body) }));
}

beforeEach(() => {
  vi.unstubAllGlobals();
  identity.signedIn = true;
});

describe("GET the Zoho Books status", () => {
  it("passes the status on, with no-store", async () => {
    stubGateway(answer(200, { connected: true, provider: "api:zoho_books" }));
    const res = await status();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ connected: true, provider: "api:zoho_books" });
    expect(calls[0].url).toBe("http://gw.test/integrations/api/zoho_books");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("sends a pick handle in its shape only, and no other name", async () => {
    stubGateway(answer(200, {}));
    await status(`?pick=${PICK}&org=other`);
    expect(calls[0].url).toBe(`http://gw.test/integrations/api/zoho_books?pick=${PICK}`);
    for (const bad of ["short", "a/../b0000000000000000", `${PICK}%26org%3Dx`]) {
      stubGateway(answer(200, {}));
      await status(`?pick=${bad}`);
      expect(calls[0].url, bad).toBe("http://gw.test/integrations/api/zoho_books");
    }
  });

  it("passes on the 404 of a dark org, so the console offers the old path", async () => {
    stubGateway(answer(404, { detail: "Not Found" }));
    expect((await status()).status).toBe(404);
  });

  it("answers a signed-out caller with 401 and calls nothing", async () => {
    identity.signedIn = false;
    stubGateway(answer(200, {}));
    expect((await status()).status).toBe(401);
    expect(calls).toHaveLength(0);
  });
});

describe("POST the Zoho Books start", () => {
  it("asks the gateway once, as the member, with no body and no query", async () => {
    stubGateway(answer(200, { url: CONSENT }));
    const res = await start({ body: JSON.stringify({ organization_id: "org-b", redirect: "https://evil.example" }) });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ url: CONSENT });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://gw.test/integrations/api/zoho_books/start");
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].init?.body).toBeUndefined();
    expect(calls[0].init?.redirect).toBe("manual");
  });

  it("refuses a gateway URL that is not a plain absolute https URL", async () => {
    for (const url of ["http://accounts.zoho.com/x", "javascript:alert(1)", "/integrations", "https://u:p@accounts.zoho.com/", 42]) {
      stubGateway(answer(200, { url }));
      const res = await start();
      expect(res.status, String(url)).toBe(502);
      expect(await res.json()).toEqual({ detail: "failed" });
    }
  });

  it("answers not_available for a dark org, and passes a short code only", async () => {
    stubGateway(answer(404, { detail: "Not Found" }));
    let res = await start();
    expect([res.status, await res.json()]).toEqual([404, { detail: "not_available" }]);
    stubGateway(answer(503, { detail: "zoho_not_configured" }));
    res = await start();
    expect([res.status, await res.json()]).toEqual([503, { detail: "zoho_not_configured" }]);
    stubGateway(answer(403, { detail: "Forbidden: missing integrations:manage" }));
    res = await start();
    expect([res.status, await res.json()]).toEqual([403, { detail: "failed" }]);
  });

  it("refuses a start that another site sends, before any call", async () => {
    for (const site of ["cross-site", "same-site", null]) {
      stubGateway(answer(200, { url: CONSENT }));
      expect((await start({ site })).status, String(site)).toBe(403);
      expect(calls).toHaveLength(0);
    }
  });

  it("turns a gateway redirect into a 502", async () => {
    stubGateway(new Response(null, { status: 302, headers: { location: "https://evil.example" } }));
    const res = await start();
    expect(res.status).toBe(502);
    expect(res.headers.get("location")).toBeNull();
  });
});

describe("POST the Zoho Books pick", () => {
  it("sends the two keys in their shapes, and drops every other key", async () => {
    stubGateway(answer(200, { connected: true }));
    const res = await pick({ pick: PICK, zoho_organization_id: "60002", organization_id: "org-b", member: "eve" });
    expect(res.status).toBe(200);
    expect(calls[0].url).toBe("http://gw.test/integrations/api/zoho_books/pick");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ pick: PICK, zoho_organization_id: "60002" });
  });

  it("refuses a bad shape with invalid_pick, and calls nothing", async () => {
    for (const body of [
      { pick: "short", zoho_organization_id: "1" },
      { pick: PICK, zoho_organization_id: "1 OR 1" },
      { pick: PICK, zoho_organization_id: 60002 },
      { pick: PICK },
      null,
    ]) {
      stubGateway(answer(200, {}));
      const res = await pick(body);
      expect(res.status, JSON.stringify(body)).toBe(400);
      expect(await res.json()).toEqual({ detail: "invalid_pick" });
      expect(calls).toHaveLength(0);
    }
  });

  it("passes the gateway's short refusal code on", async () => {
    stubGateway(answer(502, { detail: "test_read_failed" }));
    const res = await pick({ pick: PICK, zoho_organization_id: "60002" });
    expect([res.status, await res.json()]).toEqual([502, { detail: "test_read_failed" }]);
  });

  it("refuses a pick that another site sends, before any call", async () => {
    stubGateway(answer(200, {}));
    expect((await pick({ pick: PICK, zoho_organization_id: "1" }, "cross-site")).status).toBe(403);
    expect(calls).toHaveLength(0);
  });
});

describe("the three relays", () => {
  it("never log", () => {
    for (const file of ["./route.ts", "./start/route.ts", "./pick/route.ts"]) {
      const source = readFileSync(new URL(file, import.meta.url), "utf8");
      const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
      expect(code, file).not.toMatch(/console\.|logger|\blog\(/);
    }
  });
});
