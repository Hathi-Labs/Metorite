/**
 * The tenant door's member act (owner request, 2026-10-15). It must send both
 * tokens on the outgoing request and nowhere else, address the member by slug
 * and email, carry the confirmation verbatim, and fail CLOSED when any of the
 * three values is missing.
 */
import { describe, expect, it } from "vitest";
import type { FetchLike } from "./console";
import {
  removeTenantMember,
  tenantDoorConfigured,
  TenantDoorUnconfigured,
  type TenantDoorEnv,
} from "./tenantDoor";

const ENV: TenantDoorEnv = {
  url: "http://gateway.test",
  internalToken: "internal-secret",
  operatorToken: "door-secret",
};

function captureFetch(response: { status: number; body: string }) {
  const calls: { url: string; init: Parameters<FetchLike>[1] }[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, init });
    return { status: response.status, text: async () => response.body };
  };
  return { calls, fetchImpl };
}

describe("removeTenantMember", () => {
  it("DELETEs the member path with mode and the email as confirm", async () => {
    const { calls, fetchImpl } = captureFetch({ status: 200, body: "{}" });
    await removeTenantMember("acme", "ana+x@acme.test", "purge", {
      env: ENV,
      fetchImpl,
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].init.method).toBe("DELETE");
    expect(calls[0].url).toBe(
      "http://gateway.test/internal/operator/organizations/acme/members/" +
        "ana%2Bx%40acme.test?mode=purge&confirm=ana%2Bx%40acme.test",
    );
  });

  it("sends BOTH tokens, each in its own header, and no body", async () => {
    const { calls, fetchImpl } = captureFetch({ status: 200, body: "{}" });
    await removeTenantMember("acme", "ana@acme.test", "remove", {
      env: ENV,
      fetchImpl,
    });
    expect(calls[0].init.headers.Authorization).toBe("Bearer internal-secret");
    expect(calls[0].init.headers["X-Operator-Token"]).toBe("door-secret");
    expect(calls[0].init.body).toBeUndefined();
  });

  it("returns the door's status and body, and no token", async () => {
    const door = JSON.stringify({ status: "removed", email: "ana@acme.test" });
    const { fetchImpl } = captureFetch({ status: 200, body: door });
    const result = await removeTenantMember("acme", "ana@acme.test", "remove", {
      env: ENV,
      fetchImpl,
    });
    expect(result).toEqual({ status: 200, body: door });
    expect(JSON.stringify(result)).not.toContain("secret");
  });

  it("fails CLOSED, before any request, when a value is missing", async () => {
    for (const missing of ["url", "internalToken", "operatorToken"] as const) {
      const { calls, fetchImpl } = captureFetch({ status: 200, body: "{}" });
      const env = { ...ENV, [missing]: undefined };
      expect(tenantDoorConfigured(env)).toBe(false);
      await expect(
        removeTenantMember("acme", "ana@acme.test", "remove", { env, fetchImpl }),
      ).rejects.toBeInstanceOf(TenantDoorUnconfigured);
      expect(calls).toHaveLength(0);
    }
    expect(tenantDoorConfigured(ENV)).toBe(true);
  });
});
