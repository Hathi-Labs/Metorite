// `/api/studio/[…path]` (STU-F2b): a copy of the workflows proxy for the
// gateway prefix `/studio`, with PATCH added. It adds no header, no identity
// and no path rule. STU-B2c-1 adds the direct proof for a POST to the action
// route only, and forwards Retry-After.
//
// Mutations: drop `refuseUnsafePath` and the 400 test fails. Drop the PATCH
// export and the PATCH test fails. Add an `x-user-email` header and the
// source check fails. Sign every POST and the "signs only the action route"
// test fails. Drop the Retry-After copy and the 503 test fails. Drop the
// `sameOrigin` check and the "signs only a POST from a page of this origin"
// test fails (run 2026-11-03, before the fix).
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest, NextResponse } from "next/server";

const state: { identity: unknown } = { identity: { email: "a@example.com" } };
const seen: { url: string; init: RequestInit }[] = [];
let reply: () => Response = () => new Response("{}", { status: 200 });

// The real `studioDirectHeaders` runs, so the test proves what the proxy signs.
vi.mock("@/auth", () => ({ auth: async () => null, isAuthEnabled: false }));
vi.mock("@/lib/gateway", async (importActual) => ({
  ...(await importActual<typeof import("@/lib/gateway")>()),
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () =>
    state.identity ?? NextResponse.json({ error: "Sign in to continue" }, { status: 401 }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({
    Authorization: "Bearer internal",
    ...extra,
  }),
  gatewayFetch: async (url: string, init: unknown) => {
    seen.push({ url, init: init as RequestInit });
    return reply();
  },
}));

import { DELETE, GET, PATCH, POST, PUT } from "./route";

const ctx = (path?: string[]) => ({ params: Promise.resolve({ path }) });
const req = (url: string, init?: ConstructorParameters<typeof NextRequest>[1]) => new NextRequest(url, init);

const KEY = "k".repeat(40);
const APP = "6f1f7a3e-2b0f-4f43-a3a5-0d3f8f6f9c11";
// The header that the browser sets on a fetch from a page of this origin.
const SAME_ORIGIN = { "sec-fetch-site": "same-origin" };

beforeEach(() => {
  process.env.STUDIO_DIRECT_KEY = KEY;
  seen.length = 0;
  state.identity = { email: "a@example.com" };
  reply = () => new Response("{}", { status: 200 });
});

describe("/api/studio proxy", () => {
  it("sends a GET to /studio/skills and forwards the query", async () => {
    reply = () => new Response(JSON.stringify({ skills: [] }), { status: 200 });
    const res = await GET(req("http://x/api/studio/skills?limit=5"), ctx(["skills"]));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ skills: [] });
    expect(seen[0].url).toBe("http://gw.test/studio/skills?limit=5");
    expect(seen[0].init.method).toBe("GET");
    expect(seen[0].init.body).toBeUndefined();
  });

  it("sends a POST with its JSON body", async () => {
    reply = () => new Response("{}", { status: 201 });
    const res = await POST(
      req("http://x/api/studio/skills", { method: "POST", body: JSON.stringify({ name: "A" }) }),
      ctx(["skills"]),
    );
    expect(res.status).toBe(201);
    expect(seen[0].init.method).toBe("POST");
    expect(seen[0].init.body).toBe(JSON.stringify({ name: "A" }));
    expect((seen[0].init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("sends a PATCH to the state route with its body and the same headers as POST", async () => {
    await PATCH(
      req("http://x/api/studio/skills/s1/state", { method: "PATCH", body: JSON.stringify({ state: "paused" }) }),
      ctx(["skills", "s1", "state"]),
    );
    await POST(
      req("http://x/api/studio/skills/s1/versions", { method: "POST", body: "{}" }),
      ctx(["skills", "s1", "versions"]),
    );
    expect(seen[0].url).toBe("http://gw.test/studio/skills/s1/state");
    expect(seen[0].init.method).toBe("PATCH");
    expect(seen[0].init.body).toBe(JSON.stringify({ state: "paused" }));
    expect(seen[0].init.headers).toEqual(seen[1].init.headers);
  });

  it("forwards no header of the browser: the gateway call holds only the gatewayHeaders keys", async () => {
    const hostile = {
      "x-user-email": "evil@example.com",
      "x-org-id": "other-org",
      authorization: "Bearer evil",
      cookie: "session=stolen",
      "x-forwarded-for": "10.0.0.1",
    };
    await GET(req("http://x/api/studio/skills", { headers: hostile }), ctx(["skills"]));
    await PATCH(
      req("http://x/api/studio/skills/s1/state", { method: "PATCH", headers: hostile, body: "{}" }),
      ctx(["skills", "s1", "state"]),
    );
    expect(seen).toHaveLength(2);
    for (const call of seen) {
      const headers = call.init.headers as Record<string, string>;
      expect(Object.keys(headers).every((k) => k === "Authorization" || k === "Content-Type")).toBe(true);
      expect(headers.Authorization).toBe("Bearer internal");
    }
  });

  it("signs the direct proof for a POST to the action route, and for no other call", async () => {
    const action = ["apps", APP, "actions", "add"];
    const sign = vi.spyOn(Date, "now").mockReturnValue(1_790_000_000_000);
    // Each call comes from a page of this origin, so only the path and the
    // method decide here.
    const req = (url: string, init: ConstructorParameters<typeof NextRequest>[1] = {}) =>
      new NextRequest(url, { ...init, headers: { ...SAME_ORIGIN, ...(init.headers ?? {}) } });
    try {
      await POST(req("http://x/api/studio/a", { method: "POST", body: "{}" }), ctx(action));
      const proof = (seen[0].init.headers as Record<string, string>)["X-Studio-Direct"];
      const mac = createHmac("sha256", KEY)
        .update(`v1
1790000000
a@example.com
POST
/studio/apps/${APP}/actions/add`)
        .digest("hex");
      expect(proof).toBe(`v1.1790000000.${mac}`);
      seen.length = 0;
      // Another method on the action route, and other POST paths: no proof.
      await GET(req("http://x/api/studio/a"), ctx(action));
      await PATCH(req("http://x/api/studio/a", { method: "PATCH", body: "{}" }), ctx(action));
      await DELETE(req("http://x/api/studio/a", { method: "DELETE" }), ctx(action));
      await POST(req("http://x/api/studio/a", { method: "POST", body: "{}" }), ctx(["skills"]));
      await POST(req("http://x/api/studio/a", { method: "POST", body: "{}" }),
        ctx(["apps", APP, "actions"]));
      await POST(req("http://x/api/studio/a", { method: "POST", body: "{}" }),
        ctx(["apps", "not-an-id", "actions", "add"]));
      await POST(req("http://x/api/studio/a", { method: "POST", body: "{}" }),
        ctx(["apps", APP, "actions", "Add"]));
      await POST(req("http://x/api/studio/a", { method: "POST", body: "{}" }),
        ctx(["apps", APP, "actions", "add", "more"]));
      expect(seen).toHaveLength(8);
      for (const call of seen) {
        expect(Object.keys(call.init.headers as Record<string, string>)).not.toContain(
          "X-Studio-Direct");
      }
    } finally {
      sign.mockRestore();
    }
  });

  it("signs nothing when the key is unset, short or equal to the gateway token", async () => {
    const action = ["apps", APP, "actions", "add"];
    for (const key of [undefined, "short", "t".repeat(40)]) {
      if (key === undefined) delete process.env.STUDIO_DIRECT_KEY;
      else process.env.STUDIO_DIRECT_KEY = key;
      process.env.LITELLM_MASTER_KEY = "t".repeat(40);
      await POST(req("http://x/api/studio/a", { method: "POST", body: "{}", headers: SAME_ORIGIN }),
        ctx(action));
    }
    delete process.env.LITELLM_MASTER_KEY;
    expect(seen).toHaveLength(3);
    for (const call of seen) {
      expect(Object.keys(call.init.headers as Record<string, string>)).not.toContain(
        "X-Studio-Direct");
    }
  });

  it("forwards no browser header on the action route, with the proof and nothing else added", async () => {
    await POST(
      req("http://x/api/studio/a", {
        method: "POST",
        body: "{}",
        headers: { ...SAME_ORIGIN, "x-studio-direct": "v1.1.forged",
                   "x-user-email": "evil@example.com", authorization: "Bearer evil", cookie: "s=1" },
      }),
      ctx(["apps", APP, "actions", "add"]),
    );
    const headers = seen[0].init.headers as Record<string, string>;
    expect(Object.keys(headers).sort()).toEqual(
      ["Authorization", "Content-Type", "X-Studio-Direct"]);
    expect(headers["X-Studio-Direct"]).not.toContain("forged");
    expect(headers.Authorization).toBe("Bearer internal");
  });

  it("signs only a POST from a page of this origin, and forwards the others unsigned", async () => {
    // The session cookie is SameSite=Lax, so a page of another host of the
    // same site can make the browser send this POST. Unsigned, the gateway
    // answers 403 approval_required, so no write runs.
    const post = (headers: Record<string, string>) =>
      POST(req("http://x/api/studio/a", { method: "POST", body: "{}", headers }),
        ctx(["apps", APP, "actions", "add"]));
    await post(SAME_ORIGIN);
    expect(Object.keys(seen[0].init.headers as Record<string, string>)).toContain(
      "X-Studio-Direct");
    seen.length = 0;
    const others: Record<string, string>[] = [
      { origin: "https://evil.example" },
      { origin: "https://evil.example", "sec-fetch-site": "cross-site" },
      { origin: "https://ops.metorite.com", "sec-fetch-site": "same-site" },
      { "sec-fetch-site": "same-site" },
      { "sec-fetch-site": "cross-site" },
      { "sec-fetch-site": "none" },
      {},
    ];
    for (const headers of others) {
      await post(headers);
    }
    expect(seen).toHaveLength(7);
    for (const call of seen) {
      expect(call.init.method).toBe("POST");
      expect(Object.keys(call.init.headers as Record<string, string>)).not.toContain(
        "X-Studio-Direct");
    }
  });

  it("forwards Retry-After on a 503", async () => {
    reply = () =>
      new Response(JSON.stringify({ detail: { code: "store_busy" } }), {
        status: 503,
        headers: { "Retry-After": "1" },
      });
    const res = await POST(
      req("http://x/api/studio/a", { method: "POST", body: "{}" }),
      ctx(["apps", APP, "actions", "add"]),
    );
    expect(res.status).toBe(503);
    expect(res.headers.get("Retry-After")).toBe("1");
    expect(await res.json()).toEqual({ detail: { code: "store_busy" } });
  });

  it("sends no Retry-After when the gateway sends none", async () => {
    const res = await GET(req("http://x/api/studio/skills"), ctx(["skills"]));
    expect(res.headers.get("Retry-After")).toBeNull();
  });

  it("sends a PUT and a DELETE (DELETE with no body)", async () => {
    await PUT(req("http://x/api/studio/a", { method: "PUT", body: "{}" }), ctx(["a"]));
    await DELETE(req("http://x/api/studio/a", { method: "DELETE" }), ctx(["a"]));
    expect(seen.map((s) => s.init.method)).toEqual(["PUT", "DELETE"]);
    expect(seen[1].init.body).toBeUndefined();
  });

  it("forwards the JSON body of a DELETE, with its content type (the confirm token of a package)", async () => {
    await DELETE(
      req("http://x/api/studio/packages/p", { method: "DELETE", body: JSON.stringify({ confirm: "t0" }), headers: { "content-type": "application/json" } }),
      ctx(["packages", "p"]),
    );
    expect(seen[0].init.method).toBe("DELETE");
    expect(seen[0].init.body).toBe('{"confirm":"t0"}');
    expect((seen[0].init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("drops a DELETE body that is not JSON", async () => {
    await DELETE(req("http://x/api/studio/a", { method: "DELETE", body: "not json" }), ctx(["a"]));
    expect(seen[0].init.body).toBeUndefined();
  });

  it("answers 401 with no identity and calls no gateway", async () => {
    state.identity = null;
    const res = await GET(req("http://x/api/studio/skills"), ctx(["skills"]));
    expect(res.status).toBe(401);
    expect(seen).toHaveLength(0);
  });

  it("answers 400 for an unsafe path and calls no gateway", async () => {
    const res = await GET(req("http://x/api/studio/x"), ctx(["..", "internal", "drain"]));
    expect(res.status).toBe(400);
    expect(seen).toHaveLength(0);
  });

  it("passes a 409 and a 422 body through unchanged", async () => {
    const d409 = { detail: "You already have a skill with this name. Choose another name." };
    reply = () => new Response(JSON.stringify(d409), { status: 409 });
    const a = await POST(req("http://x/api/studio/skills", { method: "POST", body: "{}" }), ctx(["skills"]));
    expect(a.status).toBe(409);
    expect(await a.json()).toEqual(d409);
    const d422 = { detail: "$.actions[2]: the catalog serves no action 'x'" };
    reply = () => new Response(JSON.stringify(d422), { status: 422 });
    const b = await POST(req("http://x/api/studio/skills", { method: "POST", body: "{}" }), ctx(["skills"]));
    expect(b.status).toBe(422);
    expect(await b.json()).toEqual(d422);
  });

  it("passes a 204 through", async () => {
    reply = () => new Response(null, { status: 204 });
    const res = await DELETE(req("http://x/api/studio/a", { method: "DELETE" }), ctx(["a"]));
    expect(res.status).toBe(204);
  });

  it("answers 502 when the gateway fails", async () => {
    reply = () => {
      throw new TypeError("fetch failed");
    };
    const res = await GET(req("http://x/api/studio/skills"), ctx(["skills"]));
    expect(res.status).toBe(502);
  });

  it("adds no identity, no tenant and no header beyond gatewayHeaders and the proof (source check)", () => {
    const src = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
    expect(src.toLowerCase()).not.toContain("x-user-email");
    expect(src.toLowerCase()).not.toContain("x-studio-direct");
    expect(src).not.toContain("organization_id");
    expect(src).not.toContain("STUDIO_DIRECT_KEY");
    expect(src).toContain("refuseUnsafePath(path)");
    expect(src.match(/gatewayHeaders\(/g)).toHaveLength(1);
    expect(src.match(/studioDirectHeaders\(/g)).toHaveLength(1);
  });
});
