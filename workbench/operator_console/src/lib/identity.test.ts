/**
 * The two sign-in paths and the switch between them — WS-31 CP-12g.
 *
 * Spec: `project-docs/specs/operator_identity_and_access.md` §8 · D64.
 *
 * ⚠️ The property under test throughout is **which credential reaches the
 * Console**. On the session path it must be the CALLER's `cc_sess_` token. A
 * fallback to the shared operator token would hand every signed-in person the
 * break-glass bypass — past the §5 matrix, past the elevation window, and
 * logged as `breakglass` in every audit row.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const jar = new Map<string, string>();
const deleted: string[] = [];

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      jar.has(name) ? { name, value: jar.get(name) } : undefined,
    set: (name: string, value: string) => {
      jar.set(name, value);
    },
    delete: (name: string) => {
      deleted.push(name);
      jar.delete(name);
    },
  }),
}));

import {
  IDENTITY_FLAG,
  PROVIDER_LABELS,
  SESSION_COOKIE,
  SIGNIN_PROVIDER_FLAG,
  identityMode,
  looksLikeSession,
  providerLabel,
  signinProvider,
  usesSessions,
} from "./identity";
import { STAFF_COOKIE } from "./staff";
import { gate } from "./route";
import {
  callConsole,
  operatorHeaders,
  sessionHeaders,
  ConsoleUnconfigured,
} from "./console";

const SECRET = "interim-passphrase";
const SESSION = "cc_sess_abc123_secretpart";

beforeEach(() => {
  jar.clear();
  deleted.length = 0;
  vi.unstubAllEnvs();
});

// ── The switch ──────────────────────────────────────────────────────────────

describe("identityMode", () => {
  it("defaults to the interim path when the flag is unset", () => {
    // ⚠️ Ship dark. A flag that defaulted ON would have flipped a live console
    // to a sign-in nobody has configured yet, the moment this merged.
    expect(identityMode({})).toBe("interim");
    expect(usesSessions({})).toBe(false);
  });

  it.each(["1", "true", "TRUE", "yes", "on", " on "])(
    "reads %s as the session path",
    (value) => {
      expect(identityMode({ [IDENTITY_FLAG]: value })).toBe("session");
    },
  );

  it.each(["", "0", "false", "off", "no", "maybe"])(
    "reads %s as the interim path",
    (value) => {
      expect(identityMode({ [IDENTITY_FLAG]: value })).toBe("interim");
    },
  );
});

// ── Which directory — D70 ───────────────────────────────────────────────────

describe("signinProvider", () => {
  it("defaults to azure, so an unset variable moves nothing", () => {
    // ⚠️ Ship dark, a second time. D70 changes the directory of a console
    // that was told to change, and of no other.
    expect(signinProvider({})).toBe("azure");
    expect(providerLabel({})).toBe("Microsoft");
  });

  it.each(["google", "GOOGLE", "  google  "])("reads %s as Google", (v) => {
    expect(signinProvider({ [SIGNIN_PROVIDER_FLAG]: v })).toBe("google");
    expect(providerLabel({ [SIGNIN_PROVIDER_FLAG]: v })).toBe("Google");
  });

  it.each(["", "entra", "microsoft", "email", "okta"])(
    "falls back to the default on %s",
    (v) => {
      // The Console refuses an unknown name with a 503, so no fallback can
      // sign anybody in. This one keeps the page renderable.
      expect(signinProvider({ [SIGNIN_PROVIDER_FLAG]: v })).toBe("azure");
    },
  );

  it("⚠️ offers NO passwordless provider — D70.2", () => {
    // A console that reaches every customer organization must not admit a
    // person on inbox control alone. The Console's own allowlist is the
    // boundary; this is the half a reader can see.
    for (const bad of ["email", "magiclink", "otp", "phone", "sms"]) {
      expect(Object.keys(PROVIDER_LABELS)).not.toContain(bad);
    }
    expect(Object.keys(PROVIDER_LABELS).sort()).toEqual(["azure", "google"]);
  });
});

describe("looksLikeSession", () => {
  it("accepts only the session prefix", () => {
    expect(looksLikeSession(SESSION)).toBe(true);
    expect(looksLikeSession("cc_live_x_y")).toBe(false);
    expect(looksLikeSession(SECRET)).toBe(false);
    expect(looksLikeSession(null)).toBe(false);
    expect(looksLikeSession("")).toBe(false);
  });
});

// ── The gate, on both paths ────────────────────────────────────────────────

describe("gate on the SESSION path", () => {
  beforeEach(() => {
    vi.stubEnv(IDENTITY_FLAG, "true");
  });

  it("admits a session cookie and hands back the caller's token", async () => {
    jar.set(SESSION_COOKIE, SESSION);
    const result = await gate();
    expect(result.ok).toBe(true);
    expect(result.ok && result.authToken).toBe(SESSION);
  });

  it("refuses when there is no cookie", async () => {
    const result = await gate();
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.refusal.status).toBe(401);
  });

  it("⚠️ refuses a leftover INTERIM cookie", async () => {
    // The two cookies have different names precisely so this cannot pass.
    // A shared name would let a stale passphrase be read as a session, and
    // the console would forward a passphrase to the Console as a bearer token.
    jar.set(STAFF_COOKIE, SECRET);
    const result = await gate();
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.refusal.status).toBe(401);
  });

  it("⚠️ never falls back to the shared operator token", async () => {
    // The whole point of CP-12. If `authToken` were absent here, `callConsole`
    // would reach for `CUSTOMER_CONSOLE_OPERATOR_TOKEN` and every click would
    // be a break-glass event.
    jar.set(SESSION_COOKIE, SESSION);
    const result = await gate();
    expect(result.ok && result.authToken).toBeTruthy();
    expect(result.ok && result.authToken).not.toBe(
      process.env.CUSTOMER_CONSOLE_OPERATOR_TOKEN,
    );
  });
});

describe("gate on the INTERIM path", () => {
  beforeEach(() => {
    vi.stubEnv("OPERATOR_CONSOLE_STAFF_SECRET", SECRET);
  });

  it("admits the passphrase, and carries NO caller token", async () => {
    jar.set(STAFF_COOKIE, SECRET);
    const result = await gate();
    expect(result.ok).toBe(true);
    // There is no per-person credential on this path. That is F1.
    expect(result.ok && result.authToken).toBeUndefined();
  });

  it("refuses a wrong passphrase with 401", async () => {
    jar.set(STAFF_COOKIE, "wrong");
    const result = await gate();
    expect(result.ok === false && result.refusal.status).toBe(401);
  });

  it("fails CLOSED with 503 when no secret is configured", async () => {
    vi.stubEnv("OPERATOR_CONSOLE_STAFF_SECRET", "");
    jar.set(STAFF_COOKIE, SECRET);
    const result = await gate();
    expect(result.ok === false && result.refusal.status).toBe(503);
  });

  it("⚠️ refuses a SESSION cookie while the flag is off", async () => {
    // The mirror of the case above. A console that accepted both at once
    // would have two live doors while only one was being reasoned about.
    jar.set(SESSION_COOKIE, SESSION);
    const result = await gate();
    expect(result.ok === false && result.refusal.status).toBe(401);
  });
});

// ── Which credential actually goes on the wire ─────────────────────────────

describe("callConsole", () => {
  const env = { url: "https://console.example", operatorToken: "shared-token" };

  function spyFetch() {
    const seen: { url: string; headers: Record<string, string> }[] = [];
    return {
      seen,
      impl: async (url: string, init: { headers: Record<string, string> }) => {
        seen.push({ url, headers: init.headers });
        return { status: 200, text: async () => "{}" };
      },
    };
  }

  it("uses the CALLER's token when one is given", async () => {
    const f = spyFetch();
    await callConsole("/orgs", { method: "GET" }, {
      env,
      fetchImpl: f.impl,
      authToken: SESSION,
    });
    expect(f.seen[0].headers.Authorization).toBe(`Bearer ${SESSION}`);
    expect(f.seen[0].headers.Authorization).not.toContain("shared-token");
  });

  it("uses the shared token only when no caller token is given", async () => {
    const f = spyFetch();
    await callConsole("/orgs", { method: "GET" }, { env, fetchImpl: f.impl });
    expect(f.seen[0].headers.Authorization).toBe("Bearer shared-token");
  });

  it("⚠️ does not need the shared token at all on the session path", async () => {
    // Once real sessions exist there is no reason to keep a cross-org master
    // credential on disk, and a client that demanded one would force it.
    const f = spyFetch();
    await callConsole("/orgs", { method: "GET" }, {
      env: { url: env.url },
      fetchImpl: f.impl,
      authToken: SESSION,
    });
    expect(f.seen[0].headers.Authorization).toBe(`Bearer ${SESSION}`);
  });

  it("fails closed when the Console URL is unset, on either path", () => {
    expect(() => sessionHeaders({}, SESSION)).toThrow(ConsoleUnconfigured);
    expect(() => operatorHeaders({ operatorToken: "x" })).toThrow(
      ConsoleUnconfigured,
    );
  });
});
