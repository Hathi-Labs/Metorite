/**
 * The org-rename rules (CP-8 "Rename an organization", 2026-10-27). The BFF
 * and the Rename panel both read these, so this suite is their one fence.
 */
import { describe, expect, it } from "vitest";
import {
  mayRename,
  readRenameRequest,
  registryRenameFailure,
  renameBlocked,
  storedName,
} from "./orgRename";

describe("readRenameRequest", () => {
  it("reads the slug trimmed and the name as typed", () => {
    expect(readRenameRequest({ org_slug: " dewin ", name: "  A  B " })).toEqual({
      ok: true,
      req: { slug: "dewin", name: "  A  B " },
    });
  });

  it("passes an empty name on, because the gateway judges the name", () => {
    expect(readRenameRequest({ org_slug: "dewin", name: "" }).ok).toBe(true);
  });

  for (const body of [null, {}, { name: "X" }, { org_slug: "", name: "X" },
    { org_slug: "dewin" }, { org_slug: "dewin", name: 7 }]) {
    it(`refuses ${JSON.stringify(body)}`, () => {
      expect(readRenameRequest(body).ok).toBe(false);
    });
  }
});

describe("mayRename", () => {
  const as = (role: string | null) =>
    ({ method: "session", actor: "op@x.test", role }) as const;

  it("admits an editor, an admin and break-glass", () => {
    expect(mayRename(as("editor"))).toBeNull();
    expect(mayRename(as("admin"))).toBeNull();
    expect(mayRename({ method: "breakglass", actor: null, role: null })).toBeNull();
  });

  it("refuses a viewer, a missing role and an unknown one", () => {
    for (const role of ["viewer", null, "constructor", "__proto__"]) {
      expect(mayRename(as(role))).toContain("editor");
    }
  });
});

describe("storedName", () => {
  it("prefers the name the tenant plane stored", () => {
    expect(storedName({ display_name: "Shaurrya Tele" }, " Shaurrya  Tele")).toBe(
      "Shaurrya Tele",
    );
  });

  it("falls back to the typed name for an unreadable answer", () => {
    expect(storedName("not json", "X")).toBe("X");
    expect(storedName(null, "X")).toBe("X");
    expect(storedName({ display_name: 3 }, "X")).toBe("X");
  });
});

describe("registryRenameFailure", () => {
  it("no answer is a 502 with the retry", () => {
    for (const status of [null, 500, 503, 401, 408, 429]) {
      const f = registryRenameFailure(status, null);
      expect(f.status).toBe(502);
      expect(f.retry).toBe(true);
    }
  });

  it("a verdict keeps its status and gets no retry advice", () => {
    const f = registryRenameFailure(409, { detail: "two orgs" });
    expect(f).toMatchObject({ status: 409, retry: false });
    expect(f.error).toContain("two orgs");
    expect(f.error).not.toContain("A retry is safe");
  });
});

describe("renameBlocked", () => {
  it("is off for nothing typed and for no change", () => {
    expect(renameBlocked("   ", "Dewin")).toContain("Type");
    expect(renameBlocked(" Dewin ", "Dewin")).toContain("current");
  });

  it("is on for a new name, and judges nothing else", () => {
    expect(renameBlocked("Shaurrya Tele", "Dewin")).toBeNull();
    // The gateway refuses this one with a 422, not this panel.
    expect(renameBlocked("a".repeat(200), "Dewin")).toBeNull();
  });
});
