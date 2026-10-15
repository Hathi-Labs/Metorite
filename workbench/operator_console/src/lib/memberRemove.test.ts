import { describe, expect, it } from "vitest";
import {
  mayRemove,
  readRemoveRequest,
  refusedBy,
  registryFailure,
  removeLabel,
  requiredRole,
} from "./memberRemove";
import type { Whoami } from "./whoami";

const as = (role: string | null): Whoami => ({
  method: "session",
  actor: "op@hathilabs.com",
  role,
});
const BREAKGLASS: Whoami = { method: "breakglass", actor: null, role: null };

describe("readRemoveRequest", () => {
  it("accepts a matching confirm and trims the fields", () => {
    expect(
      readRemoveRequest({
        org_slug: " acme ",
        email: " ana@acme.test",
        mode: "purge",
        confirm: "ana@acme.test ",
      }),
    ).toEqual({
      ok: true,
      req: { slug: "acme", email: "ana@acme.test", mode: "purge" },
    });
  });

  it("refuses a confirm that differs, even only by case", () => {
    const r = readRemoveRequest({
      org_slug: "acme",
      email: "ana@acme.test",
      mode: "remove",
      confirm: "Ana@acme.test",
    });
    expect(r.ok).toBe(false);
  });

  it("refuses an unknown mode, a missing slug or email, and a non-object", () => {
    for (const body of [
      { org_slug: "acme", email: "a@b.c", mode: "delete", confirm: "a@b.c" },
      { email: "a@b.c", mode: "remove", confirm: "a@b.c" },
      { org_slug: "acme", mode: "remove", confirm: "" },
      null,
      "acme",
    ]) {
      expect(readRemoveRequest(body).ok).toBe(false);
    }
  });
});

describe("mayRemove", () => {
  it("remove needs editor, purge needs admin", () => {
    expect(requiredRole("remove")).toBe("editor");
    expect(requiredRole("purge")).toBe("admin");
    expect(mayRemove(as("viewer"), "remove", null)).not.toBeNull();
    expect(mayRemove(as("editor"), "remove", null)).toBeNull();
    expect(mayRemove(as("editor"), "purge", null)).not.toBeNull();
    expect(mayRemove(as("admin"), "purge", null)).toBeNull();
  });

  it("an unknown or missing role ranks below viewer", () => {
    expect(mayRemove(as(null), "remove", null)).not.toBeNull();
    expect(mayRemove(as("owner"), "remove", null)).not.toBeNull();
    expect(mayRemove(as("constructor"), "remove", null)).not.toBeNull();
  });

  it("a purge needs an open window only when the Console requires one", () => {
    expect(
      mayRemove(as("admin"), "purge", { elevated: false, required: false }),
    ).toBeNull();
    expect(
      mayRemove(as("admin"), "purge", { elevated: false, required: true }),
    ).toContain("elevation");
    expect(
      mayRemove(as("admin"), "purge", { elevated: true, required: true }),
    ).toBeNull();
    // A remove never asks for a window.
    expect(
      mayRemove(as("editor"), "remove", { elevated: false, required: true }),
    ).toBeNull();
  });

  it("break-glass is admitted for both modes", () => {
    expect(mayRemove(BREAKGLASS, "remove", null)).toBeNull();
    expect(mayRemove(BREAKGLASS, "purge", null)).toBeNull();
  });
});

describe("refusedBy", () => {
  it("names the tenant plane only for the route's tenant marker", () => {
    expect(
      refusedBy(JSON.stringify({ error: "x", refused_by: "tenant" })),
    ).toBe("The tenant plane");
  });

  it("keeps the Console's words for every other answer", () => {
    for (const text of [
      JSON.stringify({ detail: "seat cap reached" }),
      JSON.stringify({ error: "the tenant half succeeded", registry: "boom" }),
      JSON.stringify({ refused_by: "console" }),
      "null",
      "not json at all",
      "",
    ]) {
      expect(refusedBy(text)).toBe("The Console");
    }
  });
});

describe("registryFailure", () => {
  const E = "ana@acme.test";

  it("a fault on the way is a 502 with the retry", () => {
    for (const status of [null, 500, 502, 503]) {
      const f = registryFailure(E, status, "boom");
      expect(f.status).toBe(502);
      expect(f.retry).toBe(true);
      expect(f.error).toContain("A retry is safe");
    }
  });

  it("a 4xx verdict keeps its status and gives no retry advice", () => {
    const f = registryFailure(E, 409, { detail: "organization is deleted" });
    expect(f.status).toBe(409);
    expect(f.retry).toBe(false);
    expect(f.error).toContain("tenant half succeeded");
    expect(f.error).toContain("organization is deleted");
    expect(f.error).toContain("needs attention");
    expect(f.error).not.toContain("A retry is safe");
  });

  it("a verdict with no usable detail still says so", () => {
    expect(registryFailure(E, 403, null).error).toContain("no detail");
    expect(registryFailure(E, 404, "Not Found").error).toContain("Not Found");
  });
});

describe("removeLabel", () => {
  it("names the act for each status", () => {
    expect(removeLabel("invited")).toBe("Revoke invite");
    expect(removeLabel("active")).toBe("Remove");
    expect(removeLabel("suspended")).toBe("Remove");
    expect(removeLabel("removed")).toBeNull();
  });
});
