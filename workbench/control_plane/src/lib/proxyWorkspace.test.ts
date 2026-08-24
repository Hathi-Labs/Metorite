/**
 * WS-29 **MT-1f slice 1** — the proxy's workspace branch, EXECUTED.
 *
 * Spec: `project-docs/specs/saas_multitenancy.md` §11 MT-1f, slice-1 done-when
 * 3, 4, 5, 6 and 8 (R7 — each `describe` below names its clause).
 *
 * ⚠️ **Why this file exists beside `subdomain.test.ts`.** That file drives the
 * pure rules; this one drives `proxy()` itself, on the `authFailsClosed.test.ts`
 * pattern — `vi.mock("@/auth")` plus a stand-in request. The rules being right
 * and the proxy *asking them* are two different claims, and the second is where
 * the mistakes are: an early return placed one line too high, a lookup that runs
 * for a signed-out caller, a redirect issued as Next's default 307.
 *
 * Two things it proves that no source pin can:
 *
 * * **that the gateway is not called** on the signed-out and flag-off paths.
 *   Owner ruling B4 says an existing and an invented slug must be
 *   indistinguishable; a lookup on that path is a timing difference and a log
 *   line even when the response is identical. `fetch` is stubbed and its call
 *   count is the assertion.
 * * **that the redirect carries nothing but the apex host and the caller's own
 *   path** — asserted over the whole response (status, every header, body),
 *   rather than over the one string the code happened to build.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const posture = vi.hoisted(() => ({
  enabled: true,
  configured: true,
  session: null as { user?: { email?: string } } | null,
}));

vi.mock("@/auth", () => ({
  auth: async () => posture.session,
  get isAuthEnabled() {
    return posture.enabled;
  },
  get isAuthConfigured() {
    return posture.configured;
  },
  get isDevBypass() {
    return !posture.enabled;
  },
}));

import { proxy } from "@/proxy";

/** Every `/auth/me` call the proxy made, in order. */
let calls: Array<{ url: string; headers: Record<string, string> }> = [];
/** What the next `/auth/me` answers. `null` ⇒ an upstream failure. */
let orgSlug: string | null | undefined;

function stubFetch() {
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push({
      url: String(url),
      headers: (init?.headers ?? {}) as Record<string, string>,
    });
    if (orgSlug === null) return new Response("nope", { status: 500 });
    return new Response(
      JSON.stringify({ email: "a@b.c", organization: { slug: orgSlug } }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  });
}

function request(host: string, pathAndQuery: string) {
  return {
    nextUrl: new URL(`https://${host}${pathAndQuery}`),
    url: `https://${host}${pathAndQuery}`,
    headers: new Headers({ host }),
  } as unknown as Parameters<typeof proxy>[0];
}

function signedInAs(email: string, org: string | null | undefined) {
  posture.session = { user: { email } };
  orgSlug = org;
}

beforeEach(() => {
  posture.enabled = true;
  posture.configured = true;
  posture.session = null;
  calls = [];
  orgSlug = undefined;
  delete process.env.SUBDOMAIN_WORKSPACE_ENABLED;
  stubFetch();
});

// ══ done-when 3 · the flag, both positions ══════════════════════════════════

describe("flag OFF — byte-identical for both hosts (done-when 3)", () => {
  it("serves a signed-in member on acme.metorite.com exactly as on app.", async () => {
    signedInAs("ada@globex.example", "globex");

    const workspace = await proxy(request("acme.metorite.com", "/projects"));
    const apex = await proxy(request("app.metorite.com", "/projects"));

    expect(workspace.status).toBe(apex.status);
    expect(workspace.headers.get("location")).toBe(apex.headers.get("location"));
    expect(workspace.status).toBe(200);
  });

  it("issues NO gateway call at all — an un-flipped box does no extra work", async () => {
    signedInAs("ada@globex.example", "globex");

    await proxy(request("acme.metorite.com", "/projects"));

    expect(calls).toEqual([]);
  });

  it('is not armed by a truthy-but-wrong value', async () => {
    process.env.SUBDOMAIN_WORKSPACE_ENABLED = "false";
    signedInAs("ada@globex.example", "globex");

    const res = await proxy(request("acme.metorite.com", "/projects"));

    expect(res.status).toBe(200);
    expect(calls).toEqual([]);
  });
});

// ══ done-when 4 · the member who belongs here ═══════════════════════════════

describe("flag ON, the slug MATCHES — pass through (done-when 4)", () => {
  beforeEach(() => {
    process.env.SUBDOMAIN_WORKSPACE_ENABLED = "true";
  });

  it("serves the request unchanged", async () => {
    signedInAs("ada@acme.example", "acme");

    const res = await proxy(request("acme.metorite.com", "/projects?view=board"));

    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
  });

  it("resolves the org through the gateway as the CALLER, never from the host", async () => {
    signedInAs("ada@acme.example", "acme");

    await proxy(request("acme.metorite.com", "/projects"));

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toMatch(/\/auth\/me$/);
    // The identity is established on the way out (R11: the answer is about the
    // caller, not about the hostname), and the bearer comes from the one seam.
    expect(calls[0].headers["X-User-Email"]).toBe("ada@acme.example");
    expect(calls[0].headers["Authorization"]).toMatch(/^Bearer /);
    // The hostname is NOT sent — it is not an input to who the caller is.
    expect(JSON.stringify(calls[0])).not.toContain("acme.metorite.com");
  });

  it("leaves app.metorite.com alone and asks nothing about it", async () => {
    signedInAs("ada@acme.example", "acme");

    const res = await proxy(request("app.metorite.com", "/projects"));

    expect(res.status).toBe(200);
    expect(calls).toEqual([]);
  });

  it("leaves a RESERVED host alone — api.metorite.com is not a workspace", async () => {
    signedInAs("ada@acme.example", "acme");

    const res = await proxy(request("api.metorite.com", "/projects"));

    expect(res.status).toBe(200);
    expect(calls).toEqual([]);
  });
});

// ══ done-when 5 · the member who does not ═══════════════════════════════════

describe("flag ON, the slug DIFFERS — 302 to the apex (done-when 5)", () => {
  beforeEach(() => {
    process.env.SUBDOMAIN_WORKSPACE_ENABLED = "true";
  });

  it("302s to app.metorite.com carrying the original path and query", async () => {
    signedInAs("ada@globex.example", "globex");

    const res = await proxy(request("acme.metorite.com", "/projects?view=board"));

    // 302 exactly: Next's `redirect()` defaults to 307, which preserves the
    // METHOD — so a POST to the wrong workspace would be replayed at the apex.
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(
      "https://app.metorite.com/projects?view=board",
    );
  });

  it("NAMES NO ORGANIZATION anywhere in the response", async () => {
    signedInAs("ada@globex.example", "globex");

    const res = await proxy(request("acme.metorite.com", "/settings"));
    const whole = [
      res.status,
      [...res.headers.entries()].map(([k, v]) => `${k}: ${v}`).join("\n"),
      await res.text(),
    ].join("\n");

    // Neither the host's tenant nor the caller's. The location is the fixed
    // apex plus a path the caller already had.
    expect(whole).not.toContain("acme");
    expect(whole).not.toContain("globex");
  });

  it("treats an UNRESOLVABLE org as a mismatch — fails to the neutral apex", async () => {
    signedInAs("ada@nowhere.example", null); // /auth/me answers 500

    const res = await proxy(request("acme.metorite.com", "/projects"));

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://app.metorite.com/projects");
  });

  it("moves a signed-in stranger off the workspace's /signin too", async () => {
    // Ordering: the workspace check runs BEFORE `PUBLIC_PAGES`, or the one page
    // every wrong-host visitor lands on would be the one page that keeps them
    // there.
    signedInAs("ada@globex.example", "globex");

    const res = await proxy(request("acme.metorite.com", "/signin"));

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://app.metorite.com/signin");
  });

  it("leaves /_next and /api/auth on a workspace host untouched", async () => {
    // Ordering, the other side: static assets and the sign-in machinery pass
    // through above the check, so they neither redirect nor cost a gateway call.
    signedInAs("ada@globex.example", "globex");

    expect(
      (await proxy(request("acme.metorite.com", "/_next/static/x.js"))).status,
    ).toBe(200);
    expect(
      (await proxy(request("acme.metorite.com", "/api/auth/session"))).status,
    ).toBe(200);
    expect(calls).toEqual([]);
  });
});

// ══ done-when 6 · signed out, and telling nobody anything ═══════════════════

describe("flag ON, signed OUT — 302 to the apex, and no oracle (done-when 6)", () => {
  // ⚠️ **This whole block was rewritten on 2026-08-24 (repair round 1) and the
  // shape it used to bless was the defect.** Slice 1 shipped with the signed-out
  // caller falling through to `/signin` ON THE WORKSPACE HOST, and this file
  // asserted exactly that (a 307 whose `Location` was host-relative, compared
  // "modulo the caller's own hostname"). It is a dead end: owner ruling B2 keeps
  // every Auth.js cookie host-only on `app.<domain>`, so the OAuth `state`/`pkce`
  // cookies are written on `acme.<domain>` while B3's `AUTH_URL` pin returns the
  // callback to `app.<domain>` — Auth.js then fails the check it cannot find
  // (`InvalidCheck`); with the pin absent the same request mints a `redirect_uri`
  // on a hostname no IdP has registered. And because B2 leaves signed-out as the
  // ONLY state a workspace host can be in today, every workspace hostname was a
  // sign-in page that could not sign anybody in. The answer is now B5's redirect
  // — the same one a mismatch gets — which also STRENGTHENS B4: the `Location`
  // no longer echoes the caller's host, so the two answers are byte-identical
  // rather than identical-modulo-what-you-typed.
  beforeEach(() => {
    process.env.SUBDOMAIN_WORKSPACE_ENABLED = "true";
    posture.session = null;
  });

  it("302s to the apex carrying the original path and query", async () => {
    const res = await proxy(request("acme.metorite.com", "/projects?view=board"));

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(
      "https://app.metorite.com/projects?view=board",
    );
  });

  it("an existing and an invented slug are BYTE-IDENTICAL", async () => {
    const real = await proxy(request("acme.metorite.com", "/projects"));
    const invented = await proxy(
      request("no-such-tenant-anywhere.metorite.com", "/projects"),
    );

    // Compared to EACH OTHER rather than to a constant (the shape of
    // `test_the_invisible_cases_are_indistinguishable`,
    // `tests/unit/test_customer_console_resolve.py:737`), so a change that
    // starts telling the two apart reddens even if both stay redirects — and
    // now with NO per-host normalisation, because there is nothing host-shaped
    // left in the answer.
    const shape = async (res: Response) => ({
      status: res.status,
      headers: [...res.headers.entries()].map(([k, v]) => `${k}: ${v}`).sort(),
      body: await res.text(),
    });

    expect(await shape(real)).toEqual(await shape(invented));
    expect(real.headers.get("location")).toBe("https://app.metorite.com/projects");
  });

  it("names NEITHER slug anywhere in the response", async () => {
    const res = await proxy(request("acme.metorite.com", "/settings"));
    const whole = [
      res.status,
      [...res.headers.entries()].map(([k, v]) => `${k}: ${v}`).join("\n"),
      await res.text(),
    ].join("\n");

    expect(whole).not.toContain("acme");
  });

  it("asks the gateway NOTHING — the indistinguishability is by construction", async () => {
    await proxy(request("acme.metorite.com", "/projects"));
    await proxy(request("no-such-tenant-anywhere.metorite.com", "/projects"));

    // An answer that is identical because two lookups agreed is one a timing
    // difference or a log line can unpick. This one never asked — the whole
    // point of deciding the signed-out case before a session could be resolved
    // into a lookup.
    expect(calls).toEqual([]);
  });

  it("does NOT leave the workspace host's own /signin reachable", async () => {
    // The mutation this kills: restore the fallthrough and `/signin` on a
    // workspace host answers 307 to a host-relative location — the shape that
    // shipped, and the one that cannot complete a sign-in.
    const res = await proxy(request("acme.metorite.com", "/signin"));

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://app.metorite.com/signin");
    expect(res.headers.get("location")).not.toContain("acme.metorite.com");
  });

  it("sends a signed-out API caller to the apex too, not a host-local 401", async () => {
    // Deliberate, and a change from slice 1 as shipped. A workspace host has one
    // answer for every path in this state; a 401 here would be a second one, and
    // a signed-in mismatch on the same path already 302s. Nothing is lost: under
    // B2 a workspace host carries no session, so an API call arriving here was
    // never going to be served.
    const res = await proxy(request("acme.metorite.com", "/api/projects"));

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://app.metorite.com/api/projects");
    expect(calls).toEqual([]);
  });
});

// ══ the posture the workspace branch must not disturb ═══════════════════════

describe("the workspace branch sits INSIDE the existing posture", () => {
  it("a misconfigured production box still 503s on a workspace host", async () => {
    process.env.SUBDOMAIN_WORKSPACE_ENABLED = "true";
    posture.configured = false;
    signedInAs("ada@globex.example", "globex");

    const res = await proxy(request("acme.metorite.com", "/projects"));

    // D33.1: refuse loudly. A 302 to the apex would look like the feature
    // working on a box that cannot authenticate anybody.
    expect(res.status).toBe(503);
    expect(calls).toEqual([]);
  });

  it("a laptop with no auth is unchanged", async () => {
    process.env.SUBDOMAIN_WORKSPACE_ENABLED = "true";
    posture.enabled = false;
    posture.configured = false;

    const res = await proxy(request("acme.metorite.com", "/projects"));

    expect(res.status).toBe(200);
    expect(calls).toEqual([]);
  });
});
