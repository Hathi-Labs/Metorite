/**
 * A bearer never leaves this app without an identity attached.
 *
 * Spec: docs/multiplayer/bff-identity.md.
 *
 * The gateway reads a bearer-matched call WITHOUT identity headers as the
 * platform acting as itself and grants SERVICE_ACCESS — `*` (acb_auth/deps.py
 * §1b). So a route that forgets to attach the signed-in member does not
 * degrade to anonymous; it escalates past every `require_permission` there is.
 *
 * That is not hypothetical. It was fixed once in the memory scope guard, then
 * again in lib/memory.ts, and both times the fix was local while the shape was
 * systemic: 74 of 88 gateway-forwarding routes could emit an identity-free
 * bearer, and 38 of them sat under a `proxy.ts` public prefix and so were
 * reachable with no session at all.
 *
 * These tests are therefore in two halves:
 *
 *   1. BEHAVIOUR — the door itself fails closed.
 *   2. THE INVARIANT — a static sweep of every route file, so route 89 cannot
 *      quietly reintroduce the pattern. This half is the one that matters
 *      long-term: the first fix was correct too, and it did not hold.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// Hoisted so `vi.mock` can close over it: each test decides who is signed in.
const session = vi.hoisted(() => ({ value: null as { user?: { email?: string } } | null }));
const authEnabled = vi.hoisted(() => ({ value: true }));

vi.mock("@/auth", () => ({
  auth: async () => session.value,
  get isAuthEnabled() {
    return authEnabled.value;
  },
}));

import {
  currentIdentity,
  gatewayHeaders,
  headersActingAs,
  serviceHeaders,
  requireIdentity,
  NoIdentityError,
} from "@/lib/gateway";

beforeEach(() => {
  session.value = null;
  authEnabled.value = true;
});

// ---------------------------------------------------------------------------
// 1. The door
// ---------------------------------------------------------------------------

describe("gatewayHeaders", () => {
  it("attaches the signed-in member to the bearer", async () => {
    session.value = { user: { email: "alice@fracktal.in" } };
    const h = await gatewayHeaders();
    expect(h["X-User-Email"]).toBe("alice@fracktal.in");
    expect(h.Authorization).toMatch(/^Bearer /);
  });

  it("refuses to mint a bearer when nobody is signed in", async () => {
    // The load-bearing assertion. Previously this returned the bearer alone,
    // which the gateway reads as the platform itself — so an unauthenticated
    // request arrived upstream holding every permission.
    await expect(gatewayHeaders()).rejects.toBeInstanceOf(NoIdentityError);
  });

  it("does not let a caller override the identity through `extra`", async () => {
    // `extra` spreads last so a route can set Content-Type. It must not become
    // a way to answer "who is asking" — that is decided from the session here.
    session.value = { user: { email: "alice@fracktal.in" } };
    const h = await gatewayHeaders({ "X-User-Email": "ceo@fracktal.in" });
    expect(h["X-User-Email"]).toBe("alice@fracktal.in");
  });

  it("keeps working on a laptop with no SSO configured", async () => {
    authEnabled.value = false;
    const h = await gatewayHeaders();
    expect(h["X-User-Email"]).toBe("dev@fracktal.in");
  });

  it("treats a session with no email as nobody", async () => {
    session.value = { user: {} };
    await expect(gatewayHeaders()).rejects.toBeInstanceOf(NoIdentityError);
  });
});

describe("headersActingAs", () => {
  it("acts as the named member", () => {
    expect(headersActingAs("bob@fracktal.in")["X-User-Email"]).toBe("bob@fracktal.in");
  });

  it.each(["", "   "])("refuses a blank email (%j)", (blank) => {
    // lib/memory.ts used `if (actingEmail) h["X-User-Email"] = …`, so a blank
    // email dropped the header and sent the bearer alone — the original bug,
    // one layer down. Throwing is what makes that unrepresentable.
    expect(() => headersActingAs(blank)).toThrow(NoIdentityError);
  });
});

describe("serviceHeaders", () => {
  it("is the only way to obtain a bearer with no identity", () => {
    const h = serviceHeaders("health probe: same answer for everyone");
    expect(h.Authorization).toMatch(/^Bearer /);
    expect(h["X-User-Email"]).toBeUndefined();
  });
});

describe("requireIdentity", () => {
  it("hands back a 401 response rather than a person when nobody is signed in", async () => {
    const me = await requireIdentity();
    expect(me).toHaveProperty("status", 401);
  });

  it("hands back the person when there is one", async () => {
    session.value = { user: { email: "alice@fracktal.in" } };
    expect(await requireIdentity()).toMatchObject({ email: "alice@fracktal.in" });
  });
});

describe("currentIdentity", () => {
  it("survives auth() throwing outside a request context", async () => {
    session.value = null;
    expect(await currentIdentity()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 2. The invariant, swept across every route
// ---------------------------------------------------------------------------

const API_DIR = fileURLToPath(new URL("../app/api", import.meta.url));

function routeFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) routeFiles(full, out);
    else if (entry === "route.ts") out.push(full);
  }
  return out;
}

const ROUTES = routeFiles(API_DIR).map((path) => ({
  path,
  rel: path.slice(API_DIR.length),
  src: readFileSync(path, "utf8"),
}));

/**
 * The sweep for the two BEARER checks is wider than the route surface, and
 * WS-31 CP-2b is why.
 *
 * `auth.ts` now reaches the gateway (its `signIn` callback resolves the person
 * against the Customer Console through `headersActingAs`), and `auth.ts` is
 * outside `src/app/api/**` — so both bearer checks below were blind to it. The
 * tempting way to back away from the `auth.ts ⇄ lib/gateway.ts` import cycle is
 * to inline `GATEWAY_INTERNAL_TOKEN` in `auth.ts`; it works, and nothing here
 * could see it. `proxy.ts` joins for the same reason: it pulls `auth.ts` into
 * the proxy bundle and is the other module outside the route tree that sits on
 * the auth path.
 *
 * The thing being fenced is a SECOND BEARER READER, not the directory it lives
 * in — hence the test name below, and hence this list rather than the route
 * sweep. The remaining checks stay on ROUTES on purpose: `force-dynamic` and
 * "every handler establishes who is asking" are statements about route
 * handlers, and demanding them of `auth.ts` would be nonsense.
 *
 * ⚠️ **`lib/emailOtpAdapter.ts` joined on 2026-08-23, and it should have joined
 * with CP-2d slice 2** (repair of review finding F1). That module makes three
 * pre-session calls to the gateway through `headersActingAs`, from a tier with
 * no session — the single most tempting place in the tree to inline the bearer,
 * and the exact temptation its own docstring warns about. Slice 2 shipped that
 * docstring, and `emailOtpAdapter.test.ts`'s, both claiming the rule was
 * "fenced from both sides"; this sweep had never been widened, so the second
 * side did not exist. A double-fence nobody checked is worse than a single one.
 */
const NON_ROUTE_GATEWAY_CALLERS = [
  "../auth.ts",
  "./emailOtpAdapter.ts",
].map((rel) => {
  const path = fileURLToPath(new URL(rel, import.meta.url));
  return {
    path,
    rel: rel.replace("../", "src/").replace("./", "src/lib/"),
    src: readFileSync(path, "utf8"),
  };
});

const BEARER_SWEEP = [...ROUTES, ...NON_ROUTE_GATEWAY_CALLERS];

describe("the route surface", () => {
  it("has routes to check", () => {
    // Guards the sweep itself: a broken path would make every assertion below
    // vacuously pass, which is the classic way a scan like this rots.
    expect(ROUTES.length).toBeGreaterThan(80);
  });

  it("sweeps auth.ts and the OTP adapter too, not just the route tree", () => {
    // Guards the WIDENING itself. A renamed or moved file would make the two
    // bearer checks below silently narrow again — back to exactly the blind
    // spot CP-2b widened them to cover, and the one CP-2d slice 2 claimed to
    // have covered without ever touching this list (finding F1).
    // `src/proxy.ts` left the list under D51 (2026-08-24): the subdomain
    // workspace branch was WITHDRAWN and with it the proxy's only gateway
    // call — the proxy makes no outbound request at all now, which
    // `subdomain.test.ts`'s zero-host-reader sweep pins from the other side.
    expect(NON_ROUTE_GATEWAY_CALLERS.map((f) => f.rel)).toEqual([
      "src/auth.ts",
      "src/lib/emailOtpAdapter.ts",
    ]);
    expect(BEARER_SWEEP.length).toBe(ROUTES.length + 2);
  });

  it("no module outside lib/gateway.ts mints a gateway bearer", () => {
    // lib/gateway.ts is the only module that may read this. A module that
    // reintroduces its own copy also reintroduces the choice to omit the
    // identity, which is the whole bug — and in auth.ts's case it would also
    // be the sanctioned-looking way around an import cycle.
    const offenders = BEARER_SWEEP.filter((r) =>
      r.src.includes("GATEWAY_INTERNAL_TOKEN")
    );
    expect(offenders.map((r) => r.rel)).toEqual([]);
  });

  it("builds no Authorization header from a secret of its own", () => {
    // Two bearers in this app are legitimately built in a route, because they
    // are not the gateway's identity token and go somewhere else entirely:
    //
    //   LITELLM_KEY  the `/v1` API key — a deliberately distinct secret
    //                (deps.py: "Two secrets, deliberately distinct"), sent to
    //                the LiteLLM completions endpoint.
    //   githubToken  GitHub's own PAT, sent to api.github.com.
    //
    //   CUSTOMER_CONSOLE_ORG_KEY
    //                this deployment's OWN `cc_live_…` organization key, sent
    //                to the Control Plane (WS-31). It justifies itself on the
    //                same ground as the other two — a different secret going to
    //                a different service — and on one more that matters:
    //                the alternative was holding the Control Plane's OPERATOR
    //                token, which is cross-organization. A tenant deployment
    //                carrying that could read every customer's billing. This
    //                key can read only its own org, because the key IS the org
    //                (CP-3), so the narrow credential is the safe one here.
    //
    // Allow-listed by name rather than matched loosely, so a FOURTH inline
    // bearer fails this test and has to justify itself.
    //
    // ⚠️ `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` is deliberately NOT here and must
    // never be added: that credential is read on the GATEWAY and never in
    // Next. A fourth entry naming it would mean the deployment key had reached
    // the browser tier, and this test failing is the correct alarm (§6(f)).
    const ALLOWED = /^(LITELLM_KEY|githubToken|CUSTOMER_CONSOLE_ORG_KEY)$/;
    const offenders: string[] = [];
    for (const r of BEARER_SWEEP) {
      for (const [, name] of r.src.matchAll(/Authorization:\s*`Bearer \$\{(\w+)/g)) {
        if (!ALLOWED.test(name)) offenders.push(`${r.rel} → ${name}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("establishes who is asking wherever it reaches the gateway", () => {
    // The unit is the FUNCTION that calls gatewayHeaders, not the exported
    // handler. Counting guards against handlers looked equivalent and was not:
    // a route whose four verbs all delegate to one `forward()` needs the guard
    // in `forward`, and counting would demand four. It fired on exactly that
    // shape the first time main's workflows routes met this test.
    //
    // Note this is about ANSWERING correctly, not about safety — gatewayHeaders
    // throwing is what makes an unguarded call fail closed. Without the guard
    // that throw becomes a 502, telling a signed-out caller the gateway is down
    // rather than that they need to sign in.
    //
    // `currentIdentity` counts too: SSE routes must answer in `text/event-stream`
    // rather than JSON, and /auth/me answers a signed-out caller with a body.
    // Two arrangements both satisfy it, so the check is a disjunction:
    //   (a) every exported handler resolves, and helpers inherit that; or
    //   (b) every function that calls gatewayHeaders resolves for itself.
    // Requiring (b) alone would flag the many routes whose verbs guard and then
    // delegate to a shared `forward()`, which are perfectly safe.
    const RESOLVES = /requireIdentity\(\)|currentIdentity\(\)/;
    const offenders: string[] = [];
    for (const r of ROUTES) {
      if (!/\b(gatewayHeaders|headersActingAs)\s*\(/.test(r.src)) continue;
      const fns = r.src.split(/\n(?=(?:export )?(?:async )?function )/);

      const handlers = fns.filter((f) =>
        /^export async function (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b/.test(f)
      );
      const everyHandlerGuards =
        handlers.length > 0 && handlers.every((f) => RESOLVES.test(f));

      const touchers = fns.filter((f) =>
        /\b(gatewayHeaders|headersActingAs)\s*\(/.test(f)
      );
      const everyToucherGuards = touchers.every((f) => RESOLVES.test(f));

      if (!everyHandlerGuards && !everyToucherGuards) {
        const bad = touchers
          .filter((f) => !RESOLVES.test(f))
          .map((f) => f.match(/function (\w+)/)?.[1] ?? "?");
        offenders.push(`${r.rel} → ${bad.join(", ")}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("never resolves an identity at module scope", () => {
    // `const HEADERS = await gatewayHeaders(…)` at the top level is a
    // top-level await: it runs at IMPORT time, once per process. Two things
    // follow, and the second is the dangerous one.
    //
    // It breaks `next build` — page-data collection imports every route with
    // no request and no session, so the throw escapes and the build dies. That
    // is how this was found, on a deploy.
    //
    // And had it resolved, the headers would name whichever member happened to
    // import first, and every later request would be served as them. A
    // cross-user identity leak, from a line that looks like a constant.
    //
    // The earlier version of the sweep below printed `?()` for these files —
    // it could not name the enclosing function because there wasn't one. That
    // was the signal, unread; this is it made explicit.
    const offenders: string[] = [];
    for (const r of ROUTES) {
      for (const line of r.src.split("\n")) {
        if (/^\s*(export\s+)?(const|let|var)\s.*\bawait\s+(gatewayHeaders|headersActingAs)\s*\(/.test(line)
            && !/^\s{2,}/.test(line)) {
          offenders.push(`${r.rel}: ${line.trim()}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("marks every gateway-forwarding route dynamic", () => {
    // A route that resolves the signed-in member can never be statically
    // evaluated. Without `force-dynamic`, `next build` runs it during page-data
    // collection — with no request, and therefore no session.
    const offenders = ROUTES.filter(
      (r) =>
        /\b(gatewayHeaders|headersActingAs|requireIdentity|currentIdentity|proxyToGateway)\s*\(/.test(r.src) &&
        !/export const dynamic = "force-dynamic"/.test(r.src)
    );
    expect(offenders.map((r) => r.rel)).toEqual([]);
  });

  it("keeps every identity-free call to a written reason", () => {
    // serviceHeaders() takes a reason precisely so this is reviewable. An
    // empty string would satisfy the type and defeat the point.
    for (const r of ROUTES) {
      for (const [, reason] of r.src.matchAll(/serviceHeaders\(\s*"([^"]*)"/g)) {
        expect(reason.length, `${r.rel} gives no reason`).toBeGreaterThan(10);
      }
    }
  });
});
