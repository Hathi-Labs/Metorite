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
    // `CUSTOMER_CONSOLE_ORG_KEY` was the third entry, and H-152 took it out
    // (2026-09-28). `billing/summary/route.ts` built its bearer inline from
    // that key; the billing reads now relay through the gateway, which holds
    // the per-box deployment key. A route that builds a bearer from the
    // organization key again is the shared-box leak coming back: that key
    // names ONE tenant, and a box serves many.
    //
    // Allow-listed by name rather than matched loosely, so a THIRD inline
    // bearer fails this test and has to justify itself.
    //
    // ⚠️ `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` is deliberately NOT here and must
    // never be added: that credential is read on the GATEWAY and never in
    // Next. An entry naming it would mean the deployment key had reached
    // the browser tier, and this test failing is the correct alarm (§6(f)).
    const ALLOWED = /^(LITELLM_KEY|githubToken)$/;
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

  it("never reads the Console DEPLOYMENT key anywhere in the Next tier", () => {
    // H-152 made the gateway the only holder of the per-box deployment key:
    // the billing reads, the seat doors and the Router all reach the Console
    // through it. This sweeps EVERY source file under `src/`, not just the
    // route tree, because a helper that read the key would reach the browser
    // tier by being imported, and no route file would name it.
    const SRC_DIR = fileURLToPath(new URL("..", import.meta.url));
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) {
          walk(full);
        } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
          const src = readFileSync(full, "utf8");
          if (/process\.env\.CUSTOMER_CONSOLE_DEPLOYMENT_KEY|process\.env\[["']CUSTOMER_CONSOLE_DEPLOYMENT_KEY/.test(src)) {
            offenders.push(full);
          }
        }
      }
    };
    walk(SRC_DIR);
    expect(offenders).toEqual([]);
  });

  it("reaches the gateway only through gatewayFetch (H-194)", () => {
    // The workbench server calls the gateway on 127.0.0.1:8080, not through
    // Caddy, so Caddy's restart hold does not cover it. `gatewayFetch` is
    // what keeps a member's request alive while the gateway restarts. A bare
    // `fetch` to the gateway fails for the 10 s or more of each deploy.
    //
    // A module "reaches the gateway" when it imports GATEWAY_URL from
    // lib/gateway, or reads GATEWAY_BASE_URL, LITELLM_BASE_URL or
    // COPILOT_LLM_BASE_URL itself. The last two default to the gateway's
    // `/v1` on 127.0.0.1:8080, and the box sets them there
    // (deploy/hostinger/README.md). Such a module may not
    // call the bare `fetch` at all. The one exception is named with its
    // count, so a NEW bare fetch in that file also fails:
    //
    //   lib/memory.ts  4 calls to the legacy Mem0 server (MEM0_API_URL),
    //                  which is not the gateway.
    const ALLOWED: Record<string, number> = { "lib/memory.ts": 4 };
    const SRC_DIR = fileURLToPath(new URL("..", import.meta.url));
    const IMPORTS_URL = /import\s*\{[^}]*\bGATEWAY_URL\b[^}]*\}\s*from\s*["']@\/lib\/gateway["']/;
    const offenders: string[] = [];
    let swept = 0;
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) {
          walk(full);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(entry) || /\.test\.tsx?$/.test(entry)) continue;
        const rel = full.slice(SRC_DIR.length).replace(/\\/g, "/").replace(/^\//, "");
        if (rel === "lib/gateway.ts" || rel === "lib/gatewayFetch.ts") continue;
        const src = readFileSync(full, "utf8");
        if (
          !IMPORTS_URL.test(src) &&
          !/process\.env\.(GATEWAY_BASE_URL|LITELLM_BASE_URL|COPILOT_LLM_BASE_URL)\b/.test(src)
        ) {
          continue;
        }
        swept += 1;
        const bare = (src.match(/(?<![\w.$])fetch\(/g) ?? []).length;
        if (bare !== (ALLOWED[rel] ?? 0)) offenders.push(`${rel}: ${bare} bare fetch call(s)`);
      }
    };
    walk(SRC_DIR);
    // Guards the sweep itself: a broken walk would pass with nothing checked.
    expect(swept).toBeGreaterThan(90);
    expect(offenders).toEqual([]);
  });

  it("keeps every LLM base-URL call a POST, which does not retry (H-194)", () => {
    // LITELLM_BASE_URL and COPILOT_LLM_BASE_URL point at the gateway's `/v1`
    // on the box, but an operator can point them at an external host. A POST
    // does not retry by default, so today no call to that URL retries. A GET,
    // or a POST with `retry: true`, would retry against whatever host the URL
    // names. This test stops that change, so someone decides it on purpose.
    const LLM_CALL = /gatewayFetch\(\s*`\$\{(?:LITELLM_BASE_URL|v1Base\(\))\}[^`]*`\s*,\s*\{/g;
    // The call ends at the first line that closes the init object and the
    // call, with or without an options object: `});` or `}, { … });`.
    const CALL_END = /\n\s*\}(?:\s*,\s*\{[^}]*\})?\s*\);/;
    const found: string[] = [];
    const offenders: string[] = [];
    for (const r of ROUTES) {
      for (const m of r.src.matchAll(LLM_CALL)) {
        found.push(r.rel);
        const rest = r.src.slice(m.index! + m[0].length);
        const end = rest.match(CALL_END);
        const call = end ? rest.slice(0, end.index! + end[0].length) : rest;
        const post = /^\s*method:\s*"POST"/.test(call);
        const optsIn = /retry:\s*true/.test(call);
        if (!end || !post || optsIn) offenders.push(r.rel);
      }
    }
    // Guards the sweep: agent/chat, chat/suggestions and chat compact.
    expect(found).toHaveLength(3);
    expect(offenders).toEqual([]);
  });

  it("retries only the FINAL chat checkpoint through a restart (H-194)", () => {
    // All checkpoints of one reply share one id, and the gateway keeps the
    // last write. A periodic checkpoint that retried could land after the
    // final one and cut the stored reply short.
    const chat = ROUTES.find((r) => r.rel.replace(/\\/g, "/") === "/agent/chat/route.ts");
    expect(chat, "agent/chat/route.ts moved").toBeDefined();
    expect(chat!.src).toMatch(/\{\s*retry:\s*final\s*\}/);
    const calls = [...chat!.src.matchAll(/persistAssistantMessage\(([^;]*?)\)\.catch/g)].map((m) => m[1]);
    expect(calls).toHaveLength(2);
    expect(calls.filter((args) => /,\s*true\s*$/.test(args))).toHaveLength(1);
    // And the periodic call passes nothing after the agent, so it cannot opt
    // in through a variable either.
    expect(calls.filter((args) => /agentName\s*$/.test(args))).toHaveLength(1);
  });

  it("adds no second retry around gatewayFetch in a route (H-194)", () => {
    // Seven proxies had their own "retry a GET once" block. Around
    // gatewayFetch, such a block doubles the wait to about 50 s, and it also
    // replays a GET after a timeout or any other error.
    const RETRY_ONCE = /catch\s*\(\w*\)\s*\{\s*if\s*\(method\s*!==\s*"GET"\)\s*throw/;
    expect(ROUTES.filter((r) => RETRY_ONCE.test(r.src)).map((r) => r.rel)).toEqual([]);
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
