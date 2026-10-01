// The BFF callback of the mailbox connect flow (EM-T1a, spec
// `email_app_master_plan.md` §10.4.1, risk R-4).
//
// These cases RUN the handler. `test_email_oauth_authorize_wiring.py` reads the
// source of the same file for the shape. This file proves the behaviour: what
// goes upstream, and which `Location` the browser may follow.
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ signedIn: true }));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({ "X-User-Email": "dana@example.com" }),
  requireIdentity: async () =>
    identity.signedIn
      ? { email: "dana@example.com" }
      : NextResponse.json({ error: "Sign in to continue" }, { status: 401 }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const ORIGIN = "https://app.example.test";

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

function redirectTo(location: string): Response {
  return new Response(null, { status: 302, headers: { location } });
}

async function callback(query: string, provider = "microsoft") {
  const { GET } = await import("./route");
  const req = new NextRequest(`${ORIGIN}/api/email/oauth/${provider}/callback?${query}`);
  return GET(req, { params: Promise.resolve({ provider }) });
}

describe("the BFF email OAuth callback", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("forwards only code, state, error and error_description, as the member", async () => {
    stubGateway(redirectTo(`${ORIGIN}/email/oauth/callback?account_id=a1`));
    const res = await callback(
      "code=c0de&state=s1.g&error=x&error_description=y&user_email=evil@x.test&org=o",
    );

    expect(calls).toHaveLength(1);
    const upstream = new URL(calls[0].url);
    expect(upstream.origin + upstream.pathname).toBe(
      "http://gw.test/email/oauth/microsoft/callback",
    );
    expect([...upstream.searchParams.keys()].sort()).toEqual(
      ["code", "error", "error_description", "state"],
    );
    expect(calls[0].init?.redirect).toBe("manual");
    expect(calls[0].init?.headers).toEqual({ "X-User-Email": "dana@example.com" });

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(`${ORIGIN}/email/oauth/callback?account_id=a1`);
  });

  it("refuses a Location of another origin", async () => {
    stubGateway(redirectTo("https://evil.example.test/email/oauth/callback"));
    const res = await callback("code=c&state=s");

    const where = new URL(res.headers.get("location") ?? "");
    expect(where.origin).toBe(ORIGIN);
    expect(where.pathname).toBe("/email/oauth/callback");
    expect(where.searchParams.get("error")).toBe("callback_bad_location");
  });

  it("sends a gateway refusal to the callback page, not to the address bar", async () => {
    stubGateway(new Response(JSON.stringify({ detail: "nope" }), { status: 403 }));
    const res = await callback("code=c&state=s");

    const where = new URL(res.headers.get("location") ?? "");
    expect(where.pathname).toBe("/email/oauth/callback");
    expect(where.searchParams.get("error")).toBe("callback_failed_403");
  });

  it("does not reach the gateway for a signed-out caller", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo(`${ORIGIN}/email/oauth/callback`));
    const res = await callback("code=c&state=s");

    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("refuses a provider segment that could reach a sibling route", async () => {
    stubGateway(redirectTo(`${ORIGIN}/email/oauth/callback`));
    const res = await callback("code=c&state=s", "..%2Fsettings");

    expect(calls).toHaveLength(0);
    expect(new URL(res.headers.get("location") ?? "").searchParams.get("error")).toBe(
      "unknown_provider",
    );
  });
});
