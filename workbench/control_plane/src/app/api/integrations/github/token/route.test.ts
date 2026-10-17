// The BFF route of the Advanced GitHub token (WS-54 GH-7).
//
// Mutations, red here (run 2026-10-18):
//  T-M1 drop the `sameOrigin` refusal -> "a cross-site PUT gets 403".
//  T-M2 forward the whole body -> "only the token goes upstream".
//  T-M3 echo the body back -> "the token never comes back".
import { readFileSync } from "node:fs";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "dana@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "github-bff-test-secret-at-least-32-chars!",
}));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({ "X-User-Email": "dana@example.com" }),
  requireIdentity: async () => ({ email: "dana@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const TOKEN = "ghp_SENTINEL_TOKEN_must_not_come_back";
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

async function put(body: unknown, site = "same-origin") {
  const { PUT } = await import("./route");
  return PUT(new NextRequest("http://localhost:3001/api/integrations/github/token", {
    method: "PUT", headers: { "sec-fetch-site": site, "content-type": "application/json" },
    body: JSON.stringify(body),
  }));
}

const ok = () => new Response(JSON.stringify({ status: "connected", login: "octo", scopes: ["repo"] }), {
  status: 200,
});

describe("the BFF Advanced token", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("a cross-site PUT gets 403 and the token goes nowhere", async () => {
    stubGateway(ok());
    const res = await put({ token: TOKEN }, "cross-site");
    expect(res.status).toBe(403);
    expect(calls).toHaveLength(0);
  });

  it("only the token goes upstream", async () => {
    stubGateway(ok());
    await put({ token: ` ${TOKEN} `, org: "other-org", member: "eve@example.com" });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://gw.test/integrations/github/token");
    expect(calls[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ token: TOKEN });
  });

  it("the token never comes back", async () => {
    stubGateway(ok());
    const res = await put({ token: TOKEN });
    const text = JSON.stringify(await res.json());
    expect(text).not.toContain(TOKEN);
    expect(JSON.parse(text)).toEqual({ status: "connected", login: "octo", scopes: ["repo"] });
  });

  it("a refusal passes its short words on", async () => {
    stubGateway(new Response(JSON.stringify({ detail: "GitHub refused this token." }), { status: 422 }));
    const res = await put({ token: TOKEN });
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual({ detail: "GitHub refused this token." });
  });

  it("an empty or oversized token goes nowhere", async () => {
    stubGateway(ok());
    for (const bad of ["", "   ", "x".repeat(300), 42]) {
      const res = await put({ token: bad });
      expect(res.status).toBe(422);
    }
    expect(calls).toHaveLength(0);
  });

  it("never logs", () => {
    const source = readFileSync(new URL("./route.ts", import.meta.url), "utf-8");
    expect(source).not.toMatch(/console\.(log|info|warn|error)/);
  });
});
