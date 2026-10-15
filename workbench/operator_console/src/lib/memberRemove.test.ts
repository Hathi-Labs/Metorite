import { describe, expect, it } from "vitest";
import {
  mayRemove,
  readRemoveRequest,
  refusedBy,
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

describe("removeLabel", () => {
  it("names the act for each status", () => {
    expect(removeLabel("invited")).toBe("Revoke invite");
    expect(removeLabel("active")).toBe("Remove");
    expect(removeLabel("suspended")).toBe("Remove");
    expect(removeLabel("removed")).toBeNull();
  });
});
