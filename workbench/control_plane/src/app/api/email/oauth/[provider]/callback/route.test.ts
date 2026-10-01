// The BFF callback of the mailbox connect flow (EM-T1a, spec
// `email_app_master_plan.md` §10.4.1, risk R-4).
//
// These cases RUN the handler. `test_email_oauth_authorize_wiring.py` reads the
// source of the same file for the shape. This file proves the behaviour: what
// goes upstream, and which `Location` the browser may follow.
//
// ⚠️ Fix round 1 (F1): in production a route handler sees the bind host
// (`localhost:3001`), never the public origin. So every Location this route
// sends is RELATIVE, and the "behind the proxy" case below is the one that
// failed for every member before the fix.
import { NextRequest, NextResponse } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const PUBLIC = "https://app.example.test";
/** What a route handler sees in production, behind Caddy. */
const BIND = "http://localhost:3001";

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

async function callback(query: string, provider = "microsoft", origin = BIND) {
  const { GET } = await import("./route");
  const req = new NextRequest(`${origin}/api/email/oauth/${provider}/callback?${query}`);
  return GET(req, { params: Promise.resolve({ provider }) });
}

/** The Location as sent, and the error it carries. It must be relative. */
function landing(res: Response): { path: string; error: string | null; raw: string } {
  const raw = res.headers.get("location") ?? "";
  expect(raw.startsWith("/"), `Location must be relative, got ${raw}`).toBe(true);
  const url = new URL(raw, "https://resolved.invalid");
  return { path: url.pathname, error: url.searchParams.get("error"), raw };
}

describe("the BFF email OAuth callback", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    identity.signedIn = true;
    vi.stubEnv("WORKBENCH_PUBLIC_URL", PUBLIC);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("lands the member on the callback page behind the proxy (bind host is localhost)", async () => {
    stubGateway(redirectTo(`${PUBLIC}/email/oauth/callback?account_id=a1&provider=microsoft`));
    const res = await callback("code=c0de&state=s1.g", "microsoft", BIND);

    expect(res.status).toBe(302);
    const where = landing(res);
    expect(where.raw).toBe("/email/oauth/callback?account_id=a1&provider=microsoft");
    expect(where.error).toBeNull();
  });

  it("forwards only code, state, error and error_description, as the member", async () => {
    stubGateway(redirectTo(`${PUBLIC}/email/oauth/callback?account_id=a1`));
    await callback(
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
  });

  it("refuses a Location with another path", async () => {
    stubGateway(redirectTo("https://evil.test/x"));
    const where = landing(await callback("code=c&state=s"));
    expect(where.path).toBe("/email/oauth/callback");
    expect(where.error).toBe("callback_bad_location");
  });

  it("refuses the callback path on another origin when the public origin is set", async () => {
    stubGateway(redirectTo("https://evil.test/email/oauth/callback?account_id=a1"));
    const where = landing(await callback("code=c&state=s"));
    expect(where.error).toBe("callback_bad_location");
  });

  it("accepts the callback path when no public origin is configured", async () => {
    vi.stubEnv("WORKBENCH_PUBLIC_URL", "");
    stubGateway(redirectTo("http://localhost:3001/email/oauth/callback?account_id=a1"));
    const where = landing(await callback("code=c&state=s"));
    expect(where.raw).toBe("/email/oauth/callback?account_id=a1");
  });

  it("sends a gateway refusal to the callback page, not to the address bar", async () => {
    stubGateway(new Response(JSON.stringify({ detail: "nope" }), { status: 403 }));
    const where = landing(await callback("code=c&state=s"));
    expect(where.path).toBe("/email/oauth/callback");
    expect(where.error).toBe("callback_failed_403");
  });

  it("does not reach the gateway for a signed-out caller", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo(`${PUBLIC}/email/oauth/callback`));
    const res = await callback("code=c&state=s");

    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("refuses a provider segment that could reach a sibling route", async () => {
    stubGateway(redirectTo(`${PUBLIC}/email/oauth/callback`));
    const where = landing(await callback("code=c&state=s", "..%2Fsettings"));

    expect(calls).toHaveLength(0);
    expect(where.error).toBe("unknown_provider");
  });
});

describe("the BFF email OAuth authorize failure path (F3)", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("sends a refusal to a relative callback page, never to the bind host", async () => {
    stubGateway(new Response(JSON.stringify({ detail: "not configured" }), { status: 400 }));
    const { GET } = await import("../authorize/route");
    const req = new NextRequest(`${BIND}/api/email/oauth/microsoft/authorize`);
    const res = await GET(req, { params: Promise.resolve({ provider: "microsoft" }) });

    const where = landing(res);
    expect(where.path).toBe("/email/oauth/callback");
    expect(where.error).toBe("not configured");
    expect(where.raw).not.toContain("localhost");
  });
});
