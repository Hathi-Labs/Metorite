/**
 * MT-1k slice A2, rule 2 and the swap: the `/api/accounts/*` routes, through
 * the real handlers, with real Auth.js tokens.
 *
 * The idiom of `invite.test.ts`: mock `@/auth`, stub the gateway `fetch`, call
 * the handler. Each case is written so the obvious wrong build fails it.
 */
import { encode } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SECRET = vi.hoisted(() => "route-test-secret-at-least-32-characters!");
/** Who Auth.js says is signed in. The GET route checks it against the cookie. */
const authed = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () => (authed.email ? { user: { email: authed.email } } : null),
  isAuthEnabled: true,
  SESSION_SECRET: SECRET,
}));

import { GET } from "./route";
import { POST } from "./[action]/route";

const SESSION = "__Secure-authjs.session-token";
const HOST = "app.metorite.test";
const DAY = 86400;

/** ⚠️ `encode` sets `exp` from `maxAge`, as for a real sign-in. */
async function token(email: string, expInSeconds = DAY) {
  return encode({
    token: { email, name: email.split("@")[0] },
    salt: SESSION,
    secret: SECRET,
    maxAge: expInSeconds,
  });
}

function request(
  path: string,
  cookies: Record<string, string>,
  init: { method?: string; body?: unknown; fetchSite?: string | null } = {},
) {
  const headers = new Headers();
  if (init.fetchSite !== null) headers.set("sec-fetch-site", init.fetchSite ?? "same-origin");
  headers.set("cookie", Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; "));
  return new NextRequest(`https://${HOST}${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
}

const post = (action: string, cookies: Record<string, string>, init: Parameters<typeof request>[2] = {}) =>
  POST(request(`/api/accounts/${action}`, cookies, { method: "POST", ...init }), {
    params: Promise.resolve({ action }),
  });

/** name → value of every cookie a response sets. An empty value is a delete. */
function setCookies(res: Response): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of res.headers.getSetCookie()) {
    const [pair] = line.split(";");
    const i = pair.indexOf("=");
    out[pair.slice(0, i)] = pair.slice(i + 1);
  }
  return out;
}

beforeEach(() => {
  process.env.ACCOUNT_SWITCHER_ENABLED = "true";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      const who = new Headers(init?.headers).get("X-User-Email");
      return Response.json({
        organization: { id: `id-of-${who}`, slug: `slug-of-${who}`, display_name: `Org of ${who}` },
      });
    }),
  );
});

afterEach(() => {
  authed.email = null;
  delete process.env.ACCOUNT_SWITCHER_ENABLED;
  vi.unstubAllGlobals();
});

describe("the flag", () => {
  it("answers enabled:false and refuses every write while it is off", async () => {
    delete process.env.ACCOUNT_SWITCHER_ENABLED;
    const cookies = { [SESSION]: await token("a@one.test") };
    expect(await (await GET(request("/api/accounts", cookies))).json()).toEqual({ enabled: false });
    expect((await post("stash", cookies)).status).toBe(404);
  });
});

describe("rule 2: same origin and a good session", () => {
  it("refuses a write from another site", async () => {
    const cookies = { [SESSION]: await token("a@one.test") };
    const res = await post("stash", cookies, { fetchSite: "cross-site" });
    expect(res.status).toBe(403);
    expect(setCookies(res)).toEqual({});
  });

  it("refuses a write from a sibling subdomain, such as the operator console", async () => {
    const cookies = { [SESSION]: await token("a@one.test") };
    expect((await post("stash", cookies, { fetchSite: "same-site" })).status).toBe(403);
  });

  it("refuses a write with no Sec-Fetch-Site", async () => {
    const cookies = { [SESSION]: await token("a@one.test") };
    expect((await post("stash", cookies, { fetchSite: null })).status).toBe(403);
  });

  it("refuses a request whose session does not decode", async () => {
    expect((await post("stash", { [SESSION]: "forged" })).status).toBe(401);
    expect((await GET(request("/api/accounts", {}))).status).toBe(401);
  });
});

describe("stash, list and switch", () => {
  it("stash keeps the active token, unchanged, in a slot", async () => {
    const a = await token("a@one.test");
    const res = await post("stash", { [SESSION]: a });
    expect(res.status).toBe(200);
    expect(setCookies(res)).toEqual({ "__Host-mt-acct-0": a });
    const line = res.headers.getSetCookie()[0];
    expect(line).toMatch(/HttpOnly/i);
    expect(line).toMatch(/Secure/i);
    expect(line).toMatch(/SameSite=lax/i);
  });

  it("lists the other accounts with their organizations, never the active one", async () => {
    authed.email = "a@one.test";
    const a = await token("a@one.test");
    const b = await token("b@two.test");
    const res = await GET(
      request("/api/accounts?orgs=1", { [SESSION]: a, "__Host-mt-acct-0": a, "__Host-mt-acct-2": b }),
    );
    // R7 fence `accounts-orgs-shape`: with `?orgs=1`, each account carries
    // the id an org-aware link names, read AS that account.
    expect(await res.json()).toEqual({
      enabled: true,
      active: {
        email: "a@one.test",
        name: "a",
        organization_id: "id-of-a@one.test",
        organization_slug: "slug-of-a@one.test",
      },
      others: [
        {
          slot: 2,
          email: "b@two.test",
          name: "b",
          organization: "Org of b@two.test",
          organization_id: "id-of-b@two.test",
          organization_slug: "slug-of-b@two.test",
        },
      ],
    });
  });

  it("keeps the plain list as it was, and asks the gateway nothing", async () => {
    authed.email = "a@one.test";
    const a = await token("a@one.test");
    const b = await token("b@two.test");
    const res = await GET(request("/api/accounts", { [SESSION]: a, "__Host-mt-acct-2": b }));
    expect(await res.json()).toEqual({
      enabled: true,
      active: { email: "a@one.test", name: "a" },
      others: [{ slot: 2, email: "b@two.test", name: "b", organization: null }],
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("names no organization for an account whose read fails", async () => {
    authed.email = "a@one.test";
    vi.stubGlobal("fetch", vi.fn(async () => new Response("down", { status: 502 })));
    const a = await token("a@one.test");
    const b = await token("b@two.test");
    const body = await (await GET(request("/api/accounts?orgs=1", { [SESSION]: a, "__Host-mt-acct-1": b }))).json();
    expect(body.others).toEqual([
      { slot: 1, email: "b@two.test", name: "b", organization: null, organization_id: null, organization_slug: null },
    ]);
    expect(body.active).toEqual({ email: "a@one.test", name: "a", organization_id: null, organization_slug: null });
  });

  it("refuses the list when Auth.js names someone other than the cookie", async () => {
    authed.email = "someone@else.test";
    const res = await GET(request("/api/accounts", { [SESSION]: await token("a@one.test") }));
    expect(res.status).toBe(401);
  });

  it("switch swaps the two tokens and clears a leftover copy", async () => {
    const a = await token("a@one.test");
    const b = await token("b@two.test");
    const res = await post(
      "switch",
      { [SESSION]: a, "__Host-mt-acct-0": a, "__Host-mt-acct-1": b },
      { body: { slot: 1 } },
    );
    expect(await res.json()).toEqual({ ok: true, email: "b@two.test" });
    expect(setCookies(res)).toEqual({
      [SESSION]: b,
      "__Host-mt-acct-1": a,
      "__Host-mt-acct-0": "",
    });
  });

  it("switch refuses an empty slot, an expired one, and a slot out of range", async () => {
    const a = await token("a@one.test");
    const old = await token("c@three.test", -60);
    const cookies = { [SESSION]: a, "__Host-mt-acct-1": old };
    expect((await post("switch", cookies, { body: { slot: 0 } })).status).toBe(409);
    expect((await post("switch", cookies, { body: { slot: 1 } })).status).toBe(409);
    expect((await post("switch", cookies, { body: { slot: 9 } })).status).toBe(409);
    expect((await post("switch", cookies, { body: { slot: "1" } })).status).toBe(409);
  });

  it("signout-all clears every slot and names every account it held", async () => {
    const a = await token("a@one.test");
    const b = await token("b@two.test");
    const res = await post("signout-all", { [SESSION]: a, "__Host-mt-acct-3": b });
    expect(await res.json()).toEqual({ ok: true, emails: ["a@one.test", "b@two.test"] });
    const set = setCookies(res);
    for (let i = 0; i < 4; i++) expect(set[`__Host-mt-acct-${i}`]).toBe("");
    expect(set[SESSION]).toBeUndefined();
  });
});
