import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  callConsole,
  operatorHeaders,
  readConsoleEnv,
  listOrganizations,
  billingSummary,
  provisionOrg,
  ConsoleUnconfigured,
  exchangeSession,
  type FetchLike,
} from "./console";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = join(HERE, "..");

const ENV = {
  CUSTOMER_CONSOLE_URL: "https://console.internal",
  CUSTOMER_CONSOLE_OPERATOR_TOKEN: "op-secret-token",
};

function captureFetch(response: { status: number; body: string }) {
  const calls: { url: string; init: Parameters<FetchLike>[1] }[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, init });
    return { status: response.status, text: async () => response.body };
  };
  return { calls, fetchImpl };
}

describe("the operator token is server-side and fails closed", () => {
  it("puts the token ONLY in the outgoing Authorization header", async () => {
    const { calls, fetchImpl } = captureFetch({ status: 200, body: "{}" });
    await callConsole("/orgs", { method: "GET" }, {
      env: readConsoleEnv(ENV),
      fetchImpl,
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].init.headers.Authorization).toBe("Bearer op-secret-token");
  });

  it("never returns the token to the caller — only the Console's body", async () => {
    const consoleBody = JSON.stringify({ organizations: [] });
    const { fetchImpl } = captureFetch({ status: 200, body: consoleBody });
    const result = await callConsole("/orgs", { method: "GET" }, {
      env: readConsoleEnv(ENV),
      fetchImpl,
    });
    expect(result.body).toBe(consoleBody);
    expect(result.body).not.toContain("op-secret-token");
  });

  it("fails CLOSED when the token is unset (no unauthenticated call)", () => {
    expect(() =>
      operatorHeaders(readConsoleEnv({ CUSTOMER_CONSOLE_URL: "x" })),
    ).toThrow(ConsoleUnconfigured);
  });

  it("fails CLOSED when the URL is unset", () => {
    expect(() =>
      operatorHeaders(
        readConsoleEnv({ CUSTOMER_CONSOLE_OPERATOR_TOKEN: "t" }),
      ),
    ).toThrow(ConsoleUnconfigured);
  });

  it("targets only CUSTOMER_CONSOLE_URL (no per-deployment round trip)", async () => {
    const { calls, fetchImpl } = captureFetch({ status: 200, body: "{}" });
    await listOrganizations({ env: readConsoleEnv(ENV), fetchImpl });
    expect(calls[0].url).toBe("https://console.internal/orgs");
  });

  it("encodes the org slug on the per-org detail read", async () => {
    const { calls, fetchImpl } = captureFetch({ status: 200, body: "{}" });
    await billingSummary("a b/c", { env: readConsoleEnv(ENV), fetchImpl });
    expect(calls[0].url).toBe(
      "https://console.internal/billing/summary?org_slug=a%20b%2Fc",
    );
  });

  it("relays the Console's status verbatim (a refusal stays a refusal)", async () => {
    const { fetchImpl } = captureFetch({ status: 409, body: '{"reason":"x"}' });
    const r = await callConsole("/billing/subscriptions/activate", {
      method: "POST",
      body: { org_slug: "s" },
    }, { env: readConsoleEnv(ENV), fetchImpl });
    expect(r.status).toBe(409);
  });
});

// ── provisionOrg: create-a-customer relays the Console's answer verbatim ─────

describe("provisionOrg forwards to /orgs/provision and relays verbatim", () => {
  const body = {
    slug: "acme",
    name: "Acme Inc",
    owner_email: "owner@acme.test",
    deployment_label: "gateway",
    core_seats: 1,
  };

  it("POSTs the body to /orgs/provision with the token ONLY in the header", async () => {
    const { calls, fetchImpl } = captureFetch({
      status: 200,
      body: '{"slug":"acme"}',
    });
    const r = await provisionOrg(body, { env: readConsoleEnv(ENV), fetchImpl });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("https://console.internal/orgs/provision");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.body).toBe(JSON.stringify(body));
    expect(calls[0].init.headers.Authorization).toBe("Bearer op-secret-token");
    // The token is on the OUTGOING request only, never in the relayed body.
    expect(r.status).toBe(200);
    expect(r.body).toBe('{"slug":"acme"}');
    expect(r.body).not.toContain("op-secret-token");
  });

  it.each([
    [400, "deployment_label is required under the operator scheme"],
    [404, "no deployment 'nope'"],
    [409, "'acme' is already placed on another deployment"],
  ])("relays the Console's %i refusal unchanged", async (status, detail) => {
    const consoleBody = JSON.stringify({ detail });
    const { fetchImpl } = captureFetch({ status, body: consoleBody });
    const r = await provisionOrg(body, { env: readConsoleEnv(ENV), fetchImpl });
    expect(r.status).toBe(status);
    expect(r.body).toBe(consoleBody);
  });
});

// ── Source scan: the token cannot be bundled into client output ─────────────

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.(ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}

describe("the token never reaches a client bundle", () => {
  const files = walk(APP_ROOT).filter((f) => !f.endsWith(".test.ts"));

  it("names CUSTOMER_CONSOLE_OPERATOR_TOKEN only in server lib/console.ts", () => {
    const namers = files.filter((f) =>
      readFileSync(f, "utf-8").includes("CUSTOMER_CONSOLE_OPERATOR_TOKEN"),
    );
    // Exactly one file reads the token env, and it is the server client.
    expect(namers.map((f) => f.replace(APP_ROOT, "").replace(/\\/g, "/"))).toEqual(
      ["/lib/console.ts"],
    );
  });

  // CP-2g added TWO more server-held credentials (the gateway's internal
  // bearer + the operator door's token) — same rule, same fence, same
  // single-file discipline.
  it("names the gateway token envs only in server lib/tenantDoor.ts", () => {
    for (const name of ["GATEWAY_OPERATOR_TOKEN", "GATEWAY_INTERNAL_TOKEN"]) {
      const namers = files.filter((f) =>
        readFileSync(f, "utf-8").includes(name),
      );
      expect(
        namers.map((f) => f.replace(APP_ROOT, "").replace(/\\/g, "/")),
        name,
      ).toEqual(["/lib/tenantDoor.ts"]);
    }
  });

  it("no `use client` file imports a server credential module", () => {
    const clientFiles = files.filter((f) =>
      /^["']use client["']/.test(readFileSync(f, "utf-8").trimStart()),
    );
    for (const f of clientFiles) {
      const body = readFileSync(f, "utf-8");
      expect(body, `${f} must not import lib/console`).not.toMatch(
        /from\s+["'].*lib\/console["']/,
      );
      expect(body, `${f} must not import lib/tenantDoor`).not.toMatch(
        /from\s+["'].*lib\/tenantDoor["']/,
      );
    }
  });
});

describe("the sign-in exchange (CP-12f2)", () => {
  it("🔴 carries NO credential of ours and needs no shared token", async () => {
    // The exchange's body is the whole proof. Requiring the operator token
    // here made H-56's end-state (that token deleted) a total lockout —
    // and put the break-glass secret on the wire of every sign-in.
    const { calls, fetchImpl } = captureFetch({ status: 200, body: "{}" });
    await exchangeSession("supabase-jwt", {
      env: readConsoleEnv({ CUSTOMER_CONSOLE_URL: "https://console.internal" }),
      fetchImpl,
    });
    expect(calls).toHaveLength(1);
    expect("Authorization" in calls[0].init.headers).toBe(false);
  });
});
