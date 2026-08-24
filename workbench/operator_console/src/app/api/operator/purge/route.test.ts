/**
 * The purge route's ORDER is the contract (CP-2g), and this suite is its
 * fence: (1) the Console's org list is the authority check — an org not in
 * `deleted` stops everything; (2) the tenant plane goes first, because the
 * registry purge renames the slug the gateway door is addressed by; (3) the
 * registry purge runs only after the tenant half answered 200. The gateway
 * door itself does NOT re-check lifecycle state — this ordering is what makes
 * that safe, which is why it gets a test instead of a comment.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: string[] = [];

const listOrganizations = vi.fn();
const purgeOrgRegistry = vi.fn();
const purgeTenantOrg = vi.fn();

vi.mock("@/lib/console", () => ({
  listOrganizations: (...a: unknown[]) => {
    calls.push("list");
    return listOrganizations(...a);
  },
  purgeOrgRegistry: (...a: unknown[]) => {
    calls.push("registry");
    return purgeOrgRegistry(...a);
  },
  ConsoleUnconfigured: class ConsoleUnconfigured extends Error {},
}));

vi.mock("@/lib/tenantDoor", () => ({
  purgeTenantOrg: (...a: unknown[]) => {
    calls.push("tenant");
    return purgeTenantOrg(...a);
  },
  TenantDoorUnconfigured: class TenantDoorUnconfigured extends Error {},
}));

// The staff gate is its own suite (`staff.test.ts`); here it admits.
vi.mock("@/lib/route", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/route")>();
  return { ...real, gateStaff: async () => null };
});

import { POST } from "./route";

function req(body: unknown): Request {
  return new Request("http://operator.test/api/operator/purge", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

const DELETED_LIST = {
  status: 200,
  body: JSON.stringify({
    organizations: [{ slug: "acme", status: "deleted" }],
  }),
};

beforeEach(() => {
  calls.length = 0;
  vi.clearAllMocks();
  listOrganizations.mockResolvedValue(DELETED_LIST);
  purgeTenantOrg.mockResolvedValue({
    status: 200,
    body: JSON.stringify({ already_absent: false, deleted: {} }),
  });
  purgeOrgRegistry.mockResolvedValue({
    status: 200,
    body: JSON.stringify({ tombstone: "acme-purged-abc123", deleted: {} }),
  });
});

describe("the order", () => {
  it("authority check, then tenant, then registry — exactly once each", async () => {
    const res = await POST(req({ org_slug: "acme", confirm: "acme" }));
    expect(res.status).toBe(200);
    expect(calls).toEqual(["list", "tenant", "registry"]);
  });

  it("an org outside `deleted` stops BEFORE any destruction", async () => {
    listOrganizations.mockResolvedValue({
      status: 200,
      body: JSON.stringify({
        organizations: [{ slug: "acme", status: "active" }],
      }),
    });
    const res = await POST(req({ org_slug: "acme", confirm: "acme" }));
    expect(res.status).toBe(409);
    expect(calls).toEqual(["list"]);
  });

  it("a failed tenant purge stops BEFORE the registry purge", async () => {
    purgeTenantOrg.mockResolvedValue({ status: 502, body: "boom" });
    const res = await POST(req({ org_slug: "acme", confirm: "acme" }));
    expect(res.status).toBe(502);
    expect(calls).toEqual(["list", "tenant"]);
    const body = (await res.json()) as { error: string };
    expect(body.error).toContain("nothing was destroyed on the registry");
  });

  it("a failed registry purge names the retry, not success", async () => {
    purgeOrgRegistry.mockResolvedValue({ status: 500, body: "boom" });
    const res = await POST(req({ org_slug: "acme", confirm: "acme" }));
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error: string };
    expect(body.error).toContain("run this action again");
  });

  // Review round 1, P1: `already_absent` is ambiguous between "retry after a
  // half-failed pair" and "this console is pointed at the wrong box" — and
  // finishing the registry half in the second case destroys the only record
  // of where the data lives. The server refuses until a human decides.
  it("an absent tenant plane STOPS before the registry, until accepted", async () => {
    purgeTenantOrg.mockResolvedValue({
      status: 200,
      body: JSON.stringify({ already_absent: true, deleted: {} }),
    });
    const res = await POST(req({ org_slug: "acme", confirm: "acme" }));
    expect(res.status).toBe(409);
    expect(calls).toEqual(["list", "tenant"]);
    const body = (await res.json()) as { needs_accept_absent?: boolean };
    expect(body.needs_accept_absent).toBe(true);
  });

  it("accept_absent finishes the registry half of a confirmed retry", async () => {
    purgeTenantOrg.mockResolvedValue({
      status: 200,
      body: JSON.stringify({ already_absent: true, deleted: {} }),
    });
    const res = await POST(
      req({ org_slug: "acme", confirm: "acme", accept_absent: true }),
    );
    expect(res.status).toBe(200);
    expect(calls).toEqual(["list", "tenant", "registry"]);
  });
});

describe("the confirmation protocol", () => {
  it("a missing or mismatched confirm never reaches any upstream", async () => {
    for (const body of [
      { org_slug: "acme" },
      { org_slug: "acme", confirm: "acmee" },
      { confirm: "acme" },
    ]) {
      const res = await POST(req(body));
      expect(res.status).toBe(400);
    }
    expect(calls).toEqual([]);
  });

  it("an unknown slug is a 404 after the list read only", async () => {
    const res = await POST(req({ org_slug: "ghost", confirm: "ghost" }));
    expect(res.status).toBe(404);
    expect(calls).toEqual(["list"]);
  });
});
