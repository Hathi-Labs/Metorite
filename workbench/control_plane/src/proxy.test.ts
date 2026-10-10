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

function request(pathname: string, cookies: Record<string, string> = {}) {
  const url = `https://app.example.test${pathname}`;
  return {
    nextUrl: new URL(url),
    url,
    headers: new Headers({ host: "app.example.test" }),
    cookies: { getAll: () => Object.entries(cookies).map(([name, value]) => ({ name, value })) },
  } as unknown as Parameters<typeof proxy>[0];
}

/** The cookies a response deletes (an empty value with Max-Age=0). */
function deleted(res: Response): string[] {
  return res.headers
    .getSetCookie()
    .filter((l) => /Max-Age=0/i.test(l))
    .map((l) => l.slice(0, l.indexOf("=")))
    .sort();
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

  it("passes a signed-out GET to the update probe, and nothing under it", async () => {
    // navigation_shell.md §7.3: the sign-in page must be able to say
    // "Metorite is updating" too. Exact path only.
    const res = await proxy(request("/api/health"));
    expect(passed(res)).toBe(true);
    expect(posture.authCalls).toBe(0);
    for (const path of ["/api/health/x", "/api/healthz", "/api/health-admin"]) {
      expect((await proxy(request(path))).status, path).toBe(401);
    }
  });

  it("passes the app icons and the manifest signed out, and nothing beside them", async () => {
    // The sign-in page shows the icons to a person with no session, and a
    // browser fetches the manifest with no cookie.
    for (const path of [
      "/favicon.ico",
      "/icon.svg",
      "/apple-icon.png",
      "/manifest.webmanifest",
      "/icon-192.png",
      "/icon-512.png",
      "/icon-maskable-512.png",
      "/favicon-needs.png",
      "/favicon-reply.png",
    ]) {
      const res = await proxy(request(path));
      expect(passed(res), path).toBe(true);
    }
    for (const path of ["/icon.svg/x", "/icon-1024.png", "/public/icon-192.png"]) {
      expect(passed(await proxy(request(path))), path).toBe(false);
    }
  });

  it("does not open a subpath of the public page", async () => {
    const res = await proxy(request("/oauth/approved/x"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/signin");
  });
});

describe("proxy — the account switcher's slots end with the session (MT-1k A2, rule 4)", () => {
  const SLOTS = { "__Host-mt-acct-0": "a", "__Host-mt-acct-2": "b", "mt-acct-1": "c", other: "keep" };

  it("deletes every slot when no session is live, on a public page too", async () => {
    // The sign-in page is where a plain signOut() lands, so the sweep must run
    // before the public-page early return.
    for (const path of ["/signin", "/projects", "/api/tasks"]) {
      const res = await proxy(request(path, SLOTS));
      expect(deleted(res)).toEqual(["__Host-mt-acct-0", "__Host-mt-acct-2", "mt-acct-1"]);
    }
  });

  it("keeps the slots while a session is live, on every step of Add another account", async () => {
    // The stashed account A stays in the jar until the callback replaces it.
    posture.session = { user: { email: "a@one.test" } };
    for (const path of ["/projects", "/signin", "/api/auth/signin/google", "/api/auth/callback/google", "/signin/code"]) {
      expect(deleted(await proxy(request(path, SLOTS)))).toEqual([]);
    }
  });

  it("asks for no session when the browser holds no slot", async () => {
    await proxy(request("/signin", { other: "x" }));
    expect(posture.authCalls).toBe(0);
  });
});

describe("proxy — a signed-out link keeps its query (MT-1k, org-aware links)", () => {
  // R7 fence `proxy-callback-keeps-query`. An org-aware link carries `?org=`
  // and a record id in its query. A callback of the path alone signed the
  // member in and then opened the wrong page.
  it("sends the path AND the query to /signin as the callbackUrl", async () => {
    const link = "/projects?task=t1&org=3f2a6c1e-9b7d-4e2a-8c1f-0a1b2c3d4e5f";
    const res = await proxy(request(link));
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location") ?? "");
    expect(location.pathname).toBe("/signin");
    expect(location.searchParams.get("callbackUrl")).toBe(link);
  });

  it("sends the path alone when the link has no query", async () => {
    const res = await proxy(request("/projects"));
    const location = new URL(res.headers.get("location") ?? "");
    expect(location.searchParams.get("callbackUrl")).toBe("/projects");
  });
});
