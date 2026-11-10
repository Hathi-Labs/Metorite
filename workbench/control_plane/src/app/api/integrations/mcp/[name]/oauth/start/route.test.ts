// The BFF start of the MCP OAuth connect flow (WS-54 IN-8c).
//
// These cases RUN the handler: who may call it, what goes upstream, and that
// the browser is sent only to the https URL that the gateway built.
import { readFileSync } from "node:fs";
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ signedIn: true }));

// `sameOrigin` is the real one. Its module imports the auth config, so the
// config is a stub here, as in `relay.test.ts`.
vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "dana@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "start-test-secret-at-least-32-characters!",
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
const CONSENT = "https://mcp.notion.com/authorize?response_type=code&state=s1.g";
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

async function start(
  name = "notion",
  { site = "same-origin", body }: { site?: string | null; body?: string } = {},
) {
  const { POST } = await import("./route");
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (site) headers["sec-fetch-site"] = site;
  const req = new NextRequest(`${BIND}/api/integrations/mcp/${name}/oauth/start?org=other`, {
    method: "POST",
    headers,
    body,
  });
  return POST(req, { params: Promise.resolve({ name }) });
}

describe("the BFF MCP OAuth start", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("asks the gateway once, as the member, with no body and no query", async () => {
    stubGateway(answer(200, { url: CONSENT }));
    const res = await start("notion", {
      body: JSON.stringify({ organization_id: "org-b", member: "eve@x.test", redirect: "https://evil.example" }),
    });
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://gw.test/integrations/mcp/notion/oauth/start");
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].init?.body).toBeUndefined();
    expect(calls[0].init?.redirect).toBe("manual");
    expect((calls[0].init?.headers as Record<string, string>)["X-User-Email"]).toBe("dana@example.com");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("passes on the consent URL that the gateway built, and only that", async () => {
    stubGateway(answer(200, { url: CONSENT, extra: "dropped" }));
    const res = await start();
    expect(await res.json()).toEqual({ url: CONSENT });
  });

  it("refuses a gateway URL that is not a plain absolute https URL", async () => {
    for (const url of [
      "http://mcp.notion.com/authorize",
      "javascript:alert(1)",
      "//evil.example/authorize",
      "/integrations",
      "https://user:pw@mcp.notion.com/authorize",
      "data:text/html,hi",
      `https://mcp.notion.com/${"a".repeat(5000)}`,
      42,
      null,
    ]) {
      stubGateway(answer(200, { url }));
      const res = await start();
      expect(res.status, String(url)).toBe(502);
      expect(await res.json()).toEqual({ detail: "failed" });
    }
  });

  it("answers 404 not_available when the flag is off at the gateway", async () => {
    stubGateway(answer(404, { detail: "Not Found" }));
    const res = await start();
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ detail: "not_available" });
  });

  it("passes a short refusal code on, and never a free text", async () => {
    stubGateway(answer(502, { detail: "no_registration" }));
    let res = await start();
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ detail: "no_registration" });
    stubGateway(answer(403, { detail: "Forbidden: missing integrations:manage" }));
    res = await start();
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ detail: "failed" });
  });

  it("turns a gateway redirect into a 502, and follows nothing", async () => {
    stubGateway(new Response(null, { status: 302, headers: { location: "https://evil.example" } }));
    const res = await start();
    expect(res.status).toBe(502);
    expect(res.headers.get("location")).toBeNull();
  });

  it("answers a signed-out caller with 401 and calls nothing", async () => {
    identity.signedIn = false;
    stubGateway(answer(200, { url: CONSENT }));
    const res = await start();
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("refuses a start that another site sends, before any call", async () => {
    for (const site of ["cross-site", "same-site", null]) {
      stubGateway(answer(200, { url: CONSENT }));
      const res = await start("notion", { site });
      expect(res.status, String(site)).toBe(403);
      expect(calls).toHaveLength(0);
    }
  });

  it("refuses a path segment that could climb out of the route", async () => {
    stubGateway(answer(200, { url: CONSENT }));
    const res = await start("..");
    expect(res.status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it("fails soft when the gateway is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("down");
      }),
    );
    const res = await start();
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ detail: "unreachable" });
  });

  it("never logs, and never reads the request body", () => {
    const source = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/console\.|logger|\blog\(/);
    expect(code).not.toMatch(/req\.(json|text|formData|body|nextUrl)/);
  });
});
