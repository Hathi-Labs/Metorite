/**
 * The member-remove route's ORDER is the contract (owner request, 2026-10-15),
 * and this suite is its fence: (0) an unconfigured tenant door stops the route
 * before ANY upstream call; (1) the caller's rank is read before the tenant
 * act, because the gateway door trusts this console's token; (2) the
 * registry roster is read, and the owner hand-over is checked, before the
 * tenant act; (3) the tenant plane goes first of the two writes, because it
 * is what takes access away; (4) the Console write runs only after the
 * tenant half answered 200.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: string[] = [];

const readOperatorSession = vi.fn();
const readElevation = vi.fn();
const billingSummary = vi.fn();
const removeRegistryMember = vi.fn();
const removeTenantMember = vi.fn();
const tenantDoorConfigured = vi.fn();
const gate = vi.fn();

vi.mock("@/lib/console", () => ({
  readOperatorSession: (...a: unknown[]) => {
    calls.push("session");
    return readOperatorSession(...a);
  },
  readElevation: (...a: unknown[]) => {
    calls.push("elevation");
    return readElevation(...a);
  },
  billingSummary: (...a: unknown[]) => {
    calls.push("roster");
    return billingSummary(...a);
  },
  removeRegistryMember: (...a: unknown[]) => {
    calls.push("registry");
    return removeRegistryMember(...a);
  },
  ConsoleUnconfigured: class ConsoleUnconfigured extends Error {},
}));

vi.mock("@/lib/tenantDoor", () => {
  class TenantDoorUnconfigured extends Error {}
  return {
    removeTenantMember: (...a: unknown[]) => {
      calls.push("tenant");
      return removeTenantMember(...a);
    },
    tenantDoorConfigured: () => tenantDoorConfigured(),
    TenantDoorUnconfigured,
  };
});

// The gate has its own suites (`staff.test.ts`, `identity.test.ts`). Here it
// admits by default and hands back a caller token, so the deps threading is
// checked too.
vi.mock("@/lib/route", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/route")>();
  return { ...real, gate: () => gate() };
});

import { POST } from "./route";

const EMAIL = "ana@acme.test";
const OWNER = "owner@acme.test";
const BEN = "ben@acme.test";

function req(body: unknown): Request {
  return new Request("http://operator.test/api/operator/members/remove", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

const good = (mode: "remove" | "purge" = "remove") => ({
  org_slug: "acme",
  email: EMAIL,
  mode,
  confirm: EMAIL,
});

// The only active owner as the target.
const ownerBody = (extra: Record<string, unknown> = {}) => ({
  org_slug: "acme",
  email: OWNER,
  mode: "remove",
  confirm: OWNER,
  ...extra,
});

const ok = (body: unknown) => ({ status: 200, body: JSON.stringify(body) });

const member = (email: string, role: string, status: string) => ({
  email,
  role,
  status,
  seats: [],
});

const ROSTER = [
  member(OWNER, "owner", "active"),
  member(EMAIL, "member", "active"),
  member(BEN, "member", "active"),
  member("ivy@acme.test", "member", "invited"),
  member("old@acme.test", "member", "removed"),
];

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
  sessionAs("admin");
  readElevation.mockResolvedValue(ok({ elevated: false, required: false }));
  billingSummary.mockResolvedValue(ok({ members: ROSTER }));
  removeTenantMember.mockResolvedValue(
    ok({ status: "removed", email: EMAIL, tree_projects: 1 }),
  );
  removeRegistryMember.mockResolvedValue(
    ok({ found: true, previous_status: "active", released: ["core"] }),
  );
});

describe("the order", () => {
  it("rank, roster, then tenant, then registry — exactly once each", async () => {
    const res = await POST(req(good()));
    expect(res.status).toBe(200);
    expect(calls).toEqual(["session", "roster", "tenant", "registry"]);
    const body = (await res.json()) as {
      tenant: { status: string };
      registry: { released: string[] };
    };
    // Both halves are on the receipt.
    expect(body.tenant.status).toBe("removed");
    expect(body.registry.released).toEqual(["core"]);
  });

  it("passes slug, email and mode to the door, and the session to the Console", async () => {
    await POST(req(good("purge")));
    expect(removeTenantMember).toHaveBeenCalledWith("acme", EMAIL, "purge");
    expect(billingSummary).toHaveBeenCalledWith("acme", {
      authToken: "cc_sess_test_token",
    });
    expect(removeRegistryMember).toHaveBeenCalledWith(
      { org_slug: "acme", member_email: EMAIL },
      { authToken: "cc_sess_test_token" },
    );
    expect(readOperatorSession).toHaveBeenCalledWith({
      authToken: "cc_sess_test_token",
    });
  });

  it("an absent tenant member still finishes the registry half", async () => {
    removeTenantMember.mockResolvedValue(
      ok({ already_absent: true, email: EMAIL }),
    );
    const res = await POST(req(good()));
    expect(res.status).toBe(200);
    expect(calls).toEqual(["session", "roster", "tenant", "registry"]);
  });
});

describe("the owner hand-over (owner decision: the acting admin inherits)", () => {
  it("the only active owner with no new owner is a 400 before ANY write", async () => {
    const res = await POST(req(ownerBody()));
    expect(res.status).toBe(400);
    expect(calls).toEqual(["session", "roster"]);
    const body = (await res.json()) as {
      error: string;
      refused_by: string;
      needs_new_owner: boolean;
    };
    expect(body.error).toContain("only active owner");
    expect(body.refused_by).toBe("operator-console");
    expect(body.needs_new_owner).toBe(true);
  });

  for (const [label, newOwner] of [
    ["the target", OWNER],
    ["the target in other case", "Owner@Acme.Test"],
    ["an invited person", "ivy@acme.test"],
    ["a removed person", "old@acme.test"],
    ["a stranger", "nobody@else.test"],
  ] as const) {
    it(`a new owner who is ${label} is a 400 before ANY write`, async () => {
      const res = await POST(req(ownerBody({ new_owner_email: newOwner })));
      expect(res.status).toBe(400);
      expect(calls).toEqual(["session", "roster"]);
    });
  }

  it("a valid new owner goes through, and reaches the Console write", async () => {
    const res = await POST(req(ownerBody({ new_owner_email: BEN })));
    expect(res.status).toBe(200);
    expect(calls).toEqual(["session", "roster", "tenant", "registry"]);
    expect(removeRegistryMember).toHaveBeenCalledWith(
      { org_slug: "acme", member_email: OWNER, new_owner_email: BEN },
      { authToken: "cc_sess_test_token" },
    );
  });

  it("with two active owners, neither needs a new owner", async () => {
    billingSummary.mockResolvedValue(
      ok({ members: [...ROSTER, member("co@acme.test", "owner", "active")] }),
    );
    const res = await POST(req(ownerBody()));
    expect(res.status).toBe(200);
  });

  it("a new owner given for a non-owner is still checked", async () => {
    const res = await POST(
      req({ ...good(), new_owner_email: "nobody@else.test" }),
    );
    expect(res.status).toBe(400);
    expect(calls).not.toContain("tenant");
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
    // This refusal is the operator console's own, not the Console's.
    expect(body.refused_by).toBe("operator-console");
  });

  it("a door that throws Unconfigured late still never calls the Console write", async () => {
    const { TenantDoorUnconfigured } = await import("@/lib/tenantDoor");
    removeTenantMember.mockRejectedValue(new TenantDoorUnconfigured("x"));
    const res = await POST(req(good()));
    expect(res.status).toBe(503);
    expect(calls).not.toContain("registry");
  });

  for (const status of [401, 403, 404, 500]) {
    it(`a roster read that answers ${status} stops before the tenant act`, async () => {
      billingSummary.mockResolvedValue({ status, body: '{"detail":"no"}' });
      const res = await POST(req(good()));
      expect(res.status).toBe(status >= 500 ? 502 : status);
      expect(calls).toEqual(["session", "roster"]);
      const body = (await res.json()) as { error: string };
      expect(body.error).toContain("Nothing was changed");
    });
  }

  it("a roster read that does not answer stops before the tenant act", async () => {
    billingSummary.mockRejectedValue(new TypeError("fetch failed"));
    const res = await POST(req(good()));
    expect(res.status).toBe(502);
    expect(calls).toEqual(["session", "roster"]);
  });

  it("a roster with no member list stops before the tenant act", async () => {
    billingSummary.mockResolvedValue(ok({ seats: [] }));
    const res = await POST(req(good()));
    expect(res.status).toBe(502);
    expect(calls).toEqual(["session", "roster"]);
  });
});

describe("a tenant refusal is relayed, and stops the Console", () => {
  for (const status of [400, 401, 404, 409, 503]) {
    it(`tenant ${status} is relayed with its status`, async () => {
      removeTenantMember.mockResolvedValue({
        status,
        body: JSON.stringify({ detail: `door said ${status}` }),
      });
      const res = await POST(req(good()));
      expect(res.status).toBe(status);
      expect(calls).toEqual(["session", "roster", "tenant"]);
      const body = (await res.json()) as {
        tenant: { detail: string };
        refused_by: string;
      };
      expect(body.tenant.detail).toBe(`door said ${status}`);
      // The roster's result line reads this to name the tenant plane.
      expect(body.refused_by).toBe("tenant");
    });
  }
});

describe("a Console failure after the tenant half", () => {
  // No ANSWER (console_resolve.py's line): 5xx, 401, 408, 429, or a throw.
  for (const status of [500, 503, 401, 408, 429]) {
    it(`a Console ${status} is a 502 that names the retry`, async () => {
      removeRegistryMember.mockResolvedValue({ status, body: "boom" });
      const res = await POST(req(good()));
      expect(res.status).toBe(502);
      const body = (await res.json()) as {
        error: string;
        retry: boolean;
        refused_by?: string;
      };
      expect(body.error).toContain("tenant half succeeded");
      expect(body.error).toContain("Run this action again");
      expect(body.retry).toBe(true);
      expect(body.refused_by).toBeUndefined();
    });
  }

  it("a Console that does not answer is a 502 with the retry, and says the tenant half is done", async () => {
    removeRegistryMember.mockRejectedValue(new TypeError("fetch failed"));
    const res = await POST(req(good()));
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error: string; retry: boolean };
    expect(body.error).toContain("tenant half succeeded");
    expect(body.error).toContain("did not answer");
    expect(body.retry).toBe(true);
  });

  // Review round 1: a 4xx verdict gets no advice to retry.
  for (const status of [400, 403, 409]) {
    it(`a Console ${status} is relayed with no retry advice`, async () => {
      removeRegistryMember.mockResolvedValue({
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
      expect(body.error).toContain("tenant half succeeded");
      expect(body.error).toContain("registry refused");
      expect(body.error).toContain(`registry said ${status}`);
      expect(body.error).toContain("needs attention");
      expect(body.error).not.toContain("Run this action again");
      expect(body.error).not.toContain("A retry is safe");
      // The Console refused, not the tenant plane.
      expect(body.refused_by).toBeUndefined();
    });
  }

  it("a Console 404 says the registry has no such organization", async () => {
    removeRegistryMember.mockResolvedValue({
      status: 404,
      body: '{"detail":"Not Found"}',
    });
    const res = await POST(req(good()));
    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string; retry: boolean };
    expect(body.error).toContain('no organization "acme"');
    expect(body.retry).toBe(false);
    expect(body.error).not.toContain("A retry is safe");
  });
});

describe("the confirmation protocol", () => {
  it("a missing or mismatched confirm, email or mode never reaches any upstream", async () => {
    for (const body of [
      { org_slug: "acme", email: EMAIL, mode: "remove" },
      { org_slug: "acme", email: EMAIL, mode: "remove", confirm: "ana@acme.tes" },
      { org_slug: "acme", email: EMAIL, mode: "erase", confirm: EMAIL },
      { email: EMAIL, mode: "remove", confirm: EMAIL },
      { org_slug: "acme", mode: "remove", confirm: "" },
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

  it("a viewer is refused BEFORE the roster read and the tenant act", async () => {
    sessionAs("viewer");
    const res = await POST(req(good()));
    expect(res.status).toBe(403);
    expect(calls).toEqual(["session"]);
    const body = (await res.json()) as { refused_by: string };
    expect(body.refused_by).toBe("operator-console");
  });

  it("an editor may remove, and may NOT purge", async () => {
    sessionAs("editor");
    expect((await POST(req(good("remove")))).status).toBe(200);
    calls.length = 0;
    const res = await POST(req(good("purge")));
    expect(res.status).toBe(403);
    expect(calls).not.toContain("tenant");
  });

  it("an admin purge needs a window when the Console requires one", async () => {
    readElevation.mockResolvedValue(ok({ elevated: false, required: true }));
    const refused = await POST(req(good("purge")));
    expect(refused.status).toBe(403);
    expect(calls).toEqual(["session", "elevation"]);

    calls.length = 0;
    readElevation.mockResolvedValue(ok({ elevated: true, required: true }));
    const done = await POST(req(good("purge")));
    expect(done.status).toBe(200);
    expect(calls).toEqual([
      "session",
      "elevation",
      "roster",
      "tenant",
      "registry",
    ]);
  });

  it("break-glass is admitted, as the Console admits it", async () => {
    readOperatorSession.mockResolvedValue(
      ok({ method: "breakglass", actor: null, role: null }),
    );
    const res = await POST(req(good("purge")));
    expect(res.status).toBe(200);
    expect(calls).toEqual(["session", "roster", "tenant", "registry"]);
  });

  it("a refused session read is relayed and stops everything", async () => {
    readOperatorSession.mockResolvedValue({ status: 401, body: '{"detail":"x"}' });
    const res = await POST(req(good()));
    expect(res.status).toBe(401);
    expect(calls).toEqual(["session"]);
  });
});
