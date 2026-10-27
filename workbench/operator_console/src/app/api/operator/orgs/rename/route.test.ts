/**
 * The org-rename route's ORDER is the contract (CP-8 "Rename an organization",
 * owner request, 2026-10-27), and this suite is its fence:
 *
 *   (0) an unconfigured tenant door stops the route before ANY upstream call;
 *   (1) the caller's rank is read before the tenant act, because the gateway
 *       door trusts this console's token;
 *   (2) the TENANT rename goes first of the two writes;
 *   (3) the Console write runs only after the tenant half answered 200, and
 *       it carries the name the tenant plane stored.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: string[] = [];

const readOperatorSession = vi.fn();
const renameRegistryOrg = vi.fn();
const renameTenantOrg = vi.fn();
const tenantDoorConfigured = vi.fn();
const gate = vi.fn();

vi.mock("@/lib/console", () => ({
  readOperatorSession: (...a: unknown[]) => {
    calls.push("session");
    return readOperatorSession(...a);
  },
  renameRegistryOrg: (...a: unknown[]) => {
    calls.push("registry");
    return renameRegistryOrg(...a);
  },
  ConsoleUnconfigured: class ConsoleUnconfigured extends Error {},
}));

vi.mock("@/lib/tenantDoor", () => {
  class TenantDoorUnconfigured extends Error {}
  return {
    renameTenantOrg: (...a: unknown[]) => {
      calls.push("tenant");
      return renameTenantOrg(...a);
    },
    tenantDoorConfigured: () => tenantDoorConfigured(),
    TenantDoorUnconfigured,
  };
});

vi.mock("@/lib/route", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/route")>();
  return { ...real, gate: () => gate() };
});

import { POST } from "./route";

function req(body: unknown): Request {
  return new Request("http://operator.test/api/operator/orgs/rename", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

const good = (name = "Shaurrya Tele") => ({ org_slug: "dewin", name });

const ok = (body: unknown) => ({ status: 200, body: JSON.stringify(body) });

function sessionAs(role: string) {
  readOperatorSession.mockResolvedValue(
    ok({ method: "session", actor: "op@hathilabs.com", role }),
  );
}

beforeEach(() => {
  calls.length = 0;
  vi.clearAllMocks();
  gate.mockResolvedValue({ ok: true, authToken: "cc_sess_test_token" });
  tenantDoorConfigured.mockReturnValue(true);
  sessionAs("editor");
  renameTenantOrg.mockResolvedValue(
    ok({ slug: "dewin", organization_id: "o-1", display_name: "Shaurrya Tele" }),
  );
  renameRegistryOrg.mockResolvedValue(
    ok({ slug: "dewin", name: "Shaurrya Tele", previous_name: "Dewin" }),
  );
});

describe("the order", () => {
  it("rank, then tenant, then registry — exactly once each", async () => {
    const res = await POST(req(good()));
    expect(res.status).toBe(200);
    expect(calls).toEqual(["session", "tenant", "registry"]);
    const body = (await res.json()) as {
      tenant: { display_name: string };
      registry: { name: string };
    };
    expect(body.tenant.display_name).toBe("Shaurrya Tele");
    expect(body.registry.name).toBe("Shaurrya Tele");
  });

  it("passes the slug and name to the door, and the session to the Console", async () => {
    await POST(req(good("  Shaurrya   Tele ")));
    expect(renameTenantOrg).toHaveBeenCalledWith("dewin", "  Shaurrya   Tele ");
    expect(readOperatorSession).toHaveBeenCalledWith({
      authToken: "cc_sess_test_token",
    });
    // The Console gets the name the TENANT stored, so both planes hold one
    // value and a retry writes that value again.
    expect(renameRegistryOrg).toHaveBeenCalledWith(
      { org_slug: "dewin", name: "Shaurrya Tele" },
      { authToken: "cc_sess_test_token" },
    );
  });
});

describe("fail closed", () => {
  it("an unconfigured door answers 503 and touches NO upstream", async () => {
    tenantDoorConfigured.mockReturnValue(false);
    const res = await POST(req(good()));
    expect(res.status).toBe(503);
    expect(calls).toEqual([]);
    const body = (await res.json()) as { error: string; refused_by: string };
    expect(body.error).toContain("not configured");
    expect(body.refused_by).toBe("operator-console");
  });

  it("a door that throws Unconfigured late never calls the Console write", async () => {
    const { TenantDoorUnconfigured } = await import("@/lib/tenantDoor");
    renameTenantOrg.mockRejectedValue(new TenantDoorUnconfigured("x"));
    const res = await POST(req(good()));
    expect(res.status).toBe(503);
    expect(calls).not.toContain("registry");
  });

  it("a malformed body never reaches any upstream", async () => {
    for (const body of [
      { name: "X" },
      { org_slug: "", name: "X" },
      { org_slug: "dewin" },
      { org_slug: "dewin", name: 7 },
    ]) {
      const res = await POST(req(body));
      expect(res.status).toBe(400);
      const answer = (await res.json()) as { refused_by: string };
      expect(answer.refused_by).toBe("operator-console");
    }
    expect(calls).toEqual([]);
  });
});

describe("auth", () => {
  it("a refused gate answers its own refusal and reaches nothing", async () => {
    gate.mockResolvedValue({
      ok: false,
      refusal: new Response(JSON.stringify({ error: "operator session required" }), {
        status: 401,
      }),
    });
    const res = await POST(req(good()));
    expect(res.status).toBe(401);
    expect(calls).toEqual([]);
  });

  it("a viewer gets 403 BEFORE the tenant act", async () => {
    sessionAs("viewer");
    const res = await POST(req(good()));
    expect(res.status).toBe(403);
    expect(calls).toEqual(["session"]);
    const body = (await res.json()) as { refused_by: string };
    expect(body.refused_by).toBe("operator-console");
  });

  it("an unknown role ranks below viewer", async () => {
    sessionAs("constructor");
    const res = await POST(req(good()));
    expect(res.status).toBe(403);
    expect(calls).toEqual(["session"]);
  });

  for (const role of ["editor", "admin"]) {
    it(`an ${role} may rename`, async () => {
      sessionAs(role);
      expect((await POST(req(good()))).status).toBe(200);
    });
  }

  it("break-glass is admitted, as the Console admits it", async () => {
    readOperatorSession.mockResolvedValue(
      ok({ method: "breakglass", actor: null, role: null }),
    );
    const res = await POST(req(good()));
    expect(res.status).toBe(200);
    expect(calls).toEqual(["session", "tenant", "registry"]);
  });

  it("a refused session read is relayed and stops everything", async () => {
    readOperatorSession.mockResolvedValue({ status: 401, body: '{"detail":"x"}' });
    const res = await POST(req(good()));
    expect(res.status).toBe(401);
    expect(calls).toEqual(["session"]);
  });
});

describe("a tenant refusal is relayed, and stops the Console", () => {
  for (const status of [400, 401, 404, 422, 503]) {
    it(`tenant ${status} is relayed with its status`, async () => {
      renameTenantOrg.mockResolvedValue({
        status,
        body: JSON.stringify({ detail: `door said ${status}` }),
      });
      const res = await POST(req(good()));
      expect(res.status).toBe(status);
      expect(calls).toEqual(["session", "tenant"]);
      const body = (await res.json()) as {
        tenant: { detail: string };
        refused_by: string;
      };
      expect(body.tenant.detail).toBe(`door said ${status}`);
      expect(body.refused_by).toBe("tenant");
    });
  }
});

describe("a Console failure after the tenant half", () => {
  for (const status of [500, 503, 401, 408, 429]) {
    it(`a Console ${status} is a 502 that names the retry`, async () => {
      renameRegistryOrg.mockResolvedValue({ status, body: "boom" });
      const res = await POST(req(good()));
      expect(res.status).toBe(502);
      const body = (await res.json()) as { error: string; retry: boolean };
      expect(body.error).toContain("tenant plane is renamed");
      expect(body.error).toContain("A retry is safe");
      expect(body.retry).toBe(true);
    });
  }

  it("a Console that does not answer is a 502 with the retry", async () => {
    renameRegistryOrg.mockRejectedValue(new TypeError("fetch failed"));
    const res = await POST(req(good()));
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error: string; retry: boolean };
    expect(body.error).toContain("did not answer");
    expect(body.retry).toBe(true);
  });

  for (const status of [400, 403, 404, 409, 422]) {
    it(`a Console ${status} is relayed with no retry advice`, async () => {
      renameRegistryOrg.mockResolvedValue({
        status,
        body: JSON.stringify({ detail: `registry said ${status}` }),
      });
      const res = await POST(req(good()));
      expect(res.status).toBe(status);
      const body = (await res.json()) as {
        error: string;
        retry: boolean;
        refused_by?: string;
      };
      expect(body.retry).toBe(false);
      expect(body.error).toContain("tenant plane is renamed");
      expect(body.error).toContain(`registry said ${status}`);
      expect(body.error).not.toContain("A retry is safe");
      expect(body.refused_by).toBeUndefined();
    });
  }
});
