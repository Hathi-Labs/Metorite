/**
 * The doors of `proxy.ts` for the admin-consent return (WS-17 EM-T3c).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.3, "EM-T3c".
 *
 * An IT admin who approves Metorite has no Metorite session. Microsoft sends
 * that admin to the mail callback, and the callback sends the admin to the
 * public page `/oauth/approved`. So two paths pass the proxy signed out: that
 * page, and ONE exact API path. These cases prove that the exemption is that
 * exact path and nothing beside it. `authFailsClosed.test.ts` owns the auth
 * posture, and this file uses the same stand-in request.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const posture = vi.hoisted(() => ({
  session: null as { user?: { email?: string } } | null,
  authCalls: 0,
}));

vi.mock("@/auth", () => ({
  auth: async () => {
    posture.authCalls += 1;
    return posture.session;
  },
  isAuthEnabled: true,
  isAuthConfigured: true,
  isDevBypass: false,
}));

import { proxy } from "@/proxy";

function request(pathname: string) {
  const url = `https://app.example.test${pathname}`;
  return {
    nextUrl: new URL(url),
    url,
    headers: new Headers({ host: "app.example.test" }),
  } as unknown as Parameters<typeof proxy>[0];
}

/** `NextResponse.next()` carries this header. A refusal does not. */
function passed(res: Response): boolean {
  return res.headers.get("x-middleware-next") === "1";
}

beforeEach(() => {
  posture.session = null;
  posture.authCalls = 0;
});

describe("proxy — the admin-consent return (EM-T3c)", () => {
  it("passes a signed-out GET to the public page /oauth/approved", async () => {
    const res = await proxy(request("/oauth/approved"));
    expect(passed(res)).toBe(true);
    expect(res.status).toBe(200);
  });

  it("passes a signed-out GET to the Microsoft mail callback, with its query", async () => {
    const res = await proxy(
      request("/api/email/oauth/microsoft/callback?admin_consent=True&tenant=t"),
    );
    expect(passed(res)).toBe(true);
    expect(posture.authCalls).toBe(0);
  });

  it("still answers 401 for the authorize leg and the accounts API", async () => {
    for (const path of [
      "/api/email/oauth/microsoft/authorize",
      "/api/email/accounts",
    ]) {
      const res = await proxy(request(path));
      expect(res.status, path).toBe(401);
      expect(passed(res), path).toBe(false);
    }
  });

  it("matches the exact path only, never a prefix or a sibling", async () => {
    for (const path of [
      "/api/email/oauth/microsoft/callback/x",
      "/api/email/oauth/microsoft/callbackx",
      "/api/email/oauth/gmail/callback",
      "/api/email/oauth/microsoft/app",
    ]) {
      const res = await proxy(request(path));
      expect(res.status, path).toBe(401);
    }
  });

  it("does not open a subpath of the public page", async () => {
    const res = await proxy(request("/oauth/approved/x"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/signin");
  });
});
