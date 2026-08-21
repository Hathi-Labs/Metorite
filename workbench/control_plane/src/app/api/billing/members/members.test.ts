/**
 * SC-2b — the members read hop, session-gated and org-pinned to the key.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2b · `customer_console.md`
 * §6 item (i) (`GET /me/members`) · R11.
 *
 * The exact sibling of the SC-1a seats read hop (whose coverage lives in
 * `checkout.test.ts`'s "excluded routes" block) and built in the `manage.test.ts`
 * idiom: mock `@/auth`, stub the Console `fetch`, invoke the real handler through
 * its `@/` specifier. What it pins:
 *
 *   - **session-gated**: a signed-in member reads the roster; a signed-out one
 *     reaches nothing (401);
 *   - **org-pinned to the DEPLOYMENT's key (R11)**: it presents this deployment's
 *     own `cc_live_` key and names no tenant on the wire — no query string, no
 *     body, no `org_slug` — so the browser can only ever be shown its OWN org's
 *     members;
 *   - **fail-closed**: an unconfigured deployment is a 503, never a pass, and it
 *     holds no deployment key of its own (the key comes from `_console.ts` env).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Who is signed in is this file's subject; `currentIdentity()` resolves it
// through `auth()`.
const session = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () => (session.email ? { user: { email: session.email } } : null),
  isAuthEnabled: true,
}));

const CONSOLE_URL = "https://console.invalid";

/** Every request the stub saw, in order. */
let calls: string[] = [];

function stubFetch(status = 200, body: unknown = { members: [] }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  calls = [];
  session.email = "priya@fracktal.in";
  process.env.CUSTOMER_CONSOLE_URL = CONSOLE_URL;
  process.env.CUSTOMER_CONSOLE_ORG_KEY = "cc_live_fixture_notarealsecret";
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.CUSTOMER_CONSOLE_URL;
  delete process.env.CUSTOMER_CONSOLE_ORG_KEY;
});

describe("GET /api/billing/members", () => {
  it("reads the Console's /me/members on the deployment key, naming no org on the wire", async () => {
    stubFetch(200, {
      members: [{ email: "a@fracktal.in", role: "admin", status: "active" }],
    });
    const { GET } = await import("@/app/api/billing/members/route");
    const res = await GET();

    expect(res.status).toBe(200);
    // It reached the roster read, and no caller-named organization could have
    // moved it elsewhere: no query string, no org param.
    const membersCall = calls.find((u) => u.endsWith("/me/members"));
    expect(membersCall).toBeDefined();
    expect(membersCall).not.toMatch(/[?&]/);
    expect(await res.json()).toEqual({
      members: [{ email: "a@fracktal.in", role: "admin", status: "active" }],
    });
  });

  it("presents the deployment's OWN org key, never a browser credential", async () => {
    stubFetch();
    const { GET } = await import("@/app/api/billing/members/route");
    await GET();

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const call = fetchMock.mock.calls.find((c: unknown[]) =>
      String(c[0]).endsWith("/me/members"),
    );
    const headers = (call?.[1] as RequestInit)?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer cc_live_fixture_notarealsecret");
    // Attribution only — it refines within the org the key pinned, never moves
    // the read to another one.
    expect(headers["X-CC-Member"]).toBe("priya@fracktal.in");
  });

  it("401s a signed-out caller, and reaches the Console with nothing", async () => {
    session.email = null;
    stubFetch();
    const { GET } = await import("@/app/api/billing/members/route");
    const res = await GET();

    expect(res.status).toBe(401);
    expect(calls).toEqual([]);
  });

  it("fails CLOSED with a 503 when the deployment is not configured", async () => {
    delete process.env.CUSTOMER_CONSOLE_ORG_KEY;
    stubFetch();
    const { GET } = await import("@/app/api/billing/members/route");
    const res = await GET();

    expect(res.status).toBe(503);
    expect(calls).toEqual([]);
  });
});

describe("the members hop takes no org from the caller (R11), mirroring seats", () => {
  const SRC = readFileSync(
    fileURLToPath(new URL("./route.ts", import.meta.url)),
    "utf-8",
  );

  /** SRC with comments removed — a scan of what the code DOES, not its prose. */
  const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

  it("is a bare GET that reads no request input off the wire", () => {
    expect(SRC).toMatch(/async function GET\(\)/);
    expect(SRC).not.toMatch(/searchParams/);
    expect(SRC).not.toMatch(/req(uest)?\.(json|url|headers|nextUrl)/);
    expect(SRC).not.toMatch(/\borg_slug\b/);
    expect(SRC).not.toMatch(/\bparams\b/);
  });

  it("resolves identity server-side and pins the org to the deployment's key", () => {
    expect(SRC).toContain("currentIdentity");
    expect(SRC).toContain("consoleConfig");
    expect(SRC).toContain("consoleHeaders");
    expect(SRC).toContain("/me/members");
    expect(SRC).toContain('export const dynamic = "force-dynamic"');
  });

  it("holds no deployment key of its own — the key is `_console.ts`'s env", () => {
    // Same posture as the seats read hop: no hard-coded key, no bearer minted
    // here; the credential is read from `consoleConfig()` at call time. Scanned
    // over CODE, not the docstring, which names `cc_live_`/`CUSTOMER_CONSOLE_…`
    // in prose exactly as `seats/route.ts`'s does.
    expect(CODE).not.toContain("cc_live_");
    expect(CODE).not.toContain("CUSTOMER_CONSOLE_ORG_KEY");
    expect(CODE).not.toContain("GATEWAY_INTERNAL_TOKEN");
    expect(CODE).not.toMatch(/Authorization:\s*`Bearer/);
  });
});
