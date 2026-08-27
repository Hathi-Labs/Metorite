/**
 * The door — WS-31 CP-12g. The one ungated route, and what each path does.
 *
 * ⚠️ The properties that matter here are about a CREDENTIAL, so they are
 * asserted rather than assumed:
 *
 *  1. The `cc_sess_` token goes into an httpOnly cookie and NOT into the
 *     response body. It is a bearer credential for a cross-customer console.
 *  2. The Console's refusal is relayed VERBATIM. 401 ("we could not verify
 *     that token") and 403 ("you are not an operator") are different problems
 *     and the person in front of it must be told which.
 *  3. Signing out REVOKES the row before it clears the cookie.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const jar = new Map<string, string>();
const setCalls: { name: string; value: string; options: unknown }[] = [];
const deleted: string[] = [];

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      jar.has(name) ? { name, value: jar.get(name) } : undefined,
    set: (name: string, value: string, options: unknown) => {
      setCalls.push({ name, value, options });
      jar.set(name, value);
    },
    delete: (name: string) => {
      deleted.push(name);
      jar.delete(name);
    },
  }),
}));

const exchangeSession = vi.fn();
const revokeSession = vi.fn();

vi.mock("@/lib/console", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/console")>();
  return {
    ...real,
    exchangeSession: (...a: unknown[]) => exchangeSession(...a),
    revokeSession: (...a: unknown[]) => revokeSession(...a),
  };
});

import { DELETE, POST } from "./route";
import { IDENTITY_FLAG, SESSION_COOKIE } from "@/lib/identity";
import { STAFF_COOKIE } from "@/lib/staff";

const SESSION = "cc_sess_deadbeef_thesecret";
const SECRET = "interim-passphrase";

const post = (body: unknown) =>
  POST(
    new Request("https://console.local/api/operator/session", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  );

beforeEach(() => {
  jar.clear();
  setCalls.length = 0;
  deleted.length = 0;
  exchangeSession.mockReset();
  revokeSession.mockReset();
  vi.unstubAllEnvs();
});

describe("POST on the session path", () => {
  beforeEach(() => vi.stubEnv(IDENTITY_FLAG, "true"));

  it("refuses an empty body with 400, without calling the Console", async () => {
    const res = await post({});
    expect(res.status).toBe(400);
    expect(exchangeSession).not.toHaveBeenCalled();
  });

  it("exchanges the access token and stores the session in a cookie", async () => {
    exchangeSession.mockResolvedValue({
      status: 200,
      body: JSON.stringify({
        token: SESSION,
        expires_at: "2026-08-28T00:00:00Z",
        operator: { email: "ann@fracktal.in", role: "admin" },
      }),
    });

    const res = await post({ access_token: "supabase-token" });
    expect(res.status).toBe(200);
    expect(exchangeSession).toHaveBeenCalledWith("supabase-token");

    const cookie = setCalls.find((c) => c.name === SESSION_COOKIE);
    expect(cookie?.value).toBe(SESSION);
    expect(cookie?.options).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "lax",
    });
  });

  it("⚠️ never returns the session token to the browser", async () => {
    exchangeSession.mockResolvedValue({
      status: 200,
      body: JSON.stringify({
        token: SESSION,
        expires_at: null,
        operator: { email: "ann@fracktal.in", role: "viewer" },
      }),
    });
    const res = await post({ access_token: "t" });
    const text = await res.text();
    expect(text).not.toContain(SESSION);
    expect(text).not.toContain("cc_sess_");
    expect(JSON.parse(text).operator.email).toBe("ann@fracktal.in");
  });

  it("⚠️ drops a stale interim cookie so there is only one way in", async () => {
    jar.set(STAFF_COOKIE, SECRET);
    exchangeSession.mockResolvedValue({
      status: 200,
      body: JSON.stringify({
        token: SESSION,
        expires_at: null,
        operator: { email: "a@b.c", role: "viewer" },
      }),
    });
    await post({ access_token: "t" });
    expect(deleted).toContain(STAFF_COOKIE);
  });

  it.each([
    [401, "we could not verify that token"],
    [403, "not a platform operator"],
    [503, "not configured"],
  ])("relays the Console's %s verbatim", async (status, detail) => {
    exchangeSession.mockResolvedValue({
      status,
      body: JSON.stringify({ detail }),
    });
    const res = await post({ access_token: "t" });
    expect(res.status).toBe(status);
    expect(JSON.parse(await res.text()).detail).toBe(detail);
    // Nothing was stored on a refusal.
    expect(setCalls.find((c) => c.name === SESSION_COOKIE)).toBeUndefined();
  });
});

describe("POST on the interim path", () => {
  beforeEach(() => {
    vi.stubEnv("OPERATOR_CONSOLE_STAFF_SECRET", SECRET);
  });

  it("still works, because the console is live on it today", async () => {
    const res = await post({ secret: SECRET });
    expect(res.status).toBe(200);
    expect(setCalls.find((c) => c.name === STAFF_COOKIE)?.value).toBe(SECRET);
    expect(exchangeSession).not.toHaveBeenCalled();
  });

  it("refuses a wrong passphrase", async () => {
    const res = await post({ secret: "nope" });
    expect(res.status).toBe(401);
  });

  it("⚠️ ignores an access_token while the flag is off", async () => {
    // Otherwise flipping the flag would be reversible only in one direction,
    // and a half-configured box would have two doors at once.
    const res = await post({ access_token: "supabase-token" });
    expect(res.status).toBe(401);
    expect(exchangeSession).not.toHaveBeenCalled();
  });
});

describe("DELETE", () => {
  it("revokes the row server-side before clearing the cookie", async () => {
    vi.stubEnv(IDENTITY_FLAG, "true");
    jar.set(SESSION_COOKIE, SESSION);
    revokeSession.mockResolvedValue({ status: 200, body: "{}" });

    const res = await DELETE();
    expect(res.status).toBe(200);
    // With the caller's OWN token — the Console revokes the session that made
    // the request, not every session that operator holds.
    expect(revokeSession).toHaveBeenCalledWith({ authToken: SESSION });
    expect(deleted).toContain(SESSION_COOKIE);
  });

  it("clears both cookies even with nothing to revoke", async () => {
    const res = await DELETE();
    expect(res.status).toBe(200);
    expect(deleted).toContain(SESSION_COOKIE);
    expect(deleted).toContain(STAFF_COOKIE);
  });

  it("⚠️ the interim path can only ask the browser to forget — that is F5", async () => {
    vi.stubEnv("OPERATOR_CONSOLE_STAFF_SECRET", SECRET);
    jar.set(STAFF_COOKIE, SECRET);
    await DELETE();
    expect(revokeSession).not.toHaveBeenCalled();
    // The passphrase is still valid for everybody, including this browser if
    // it is typed again. Nothing here can change that, and that is the point.
  });
});
