import { describe, expect, it } from "vitest";

import { adminsOnly, denialOf, groupSubject, isNotFound, refusalOf, toggleGroup } from "./access";
import { CrmError } from "./api";
import { firstOpen } from "./connect";

describe("the CRM refusals (CRM-L2)", () => {
  const ACCESS = "Forbidden: an admin of your organization has not given you access to the CRM.";
  it("tells the access rule 403 from the missing feature 403", () => {
    expect(denialOf(new CrmError(ACCESS, 403))).toBe("access");
    expect(denialOf(new CrmError("Forbidden: missing permission 'feature:crm'.", 403))).toBe("feature");
    expect(denialOf(new CrmError("Not Found", 404))).toBeNull();
    expect(denialOf(new Error("network"))).toBeNull();
  });

  it("reads a 404 as the CRM being off", () => {
    expect(isNotFound(new CrmError("Not Found", 404))).toBe(true);
    expect(isNotFound(new CrmError("Forbidden", 403))).toBe(false);
  });

  it("draws one state for each refusal once the read is done, and a skeleton before", () => {
    const base = { connection: null, settingsMode: undefined, isAdmin: false };
    expect(firstOpen({ ...base, loading: false, denied: "access" })).toBe("no-access");
    expect(firstOpen({ ...base, loading: false, denied: "feature" })).toBe("no-feature");
    expect(firstOpen({ ...base, loading: false, off: true })).toBe("off");
    expect(firstOpen({ ...base, loading: true, denied: "access" })).toBe("loading");
    expect(firstOpen({ ...base, loading: false })).toBe("ready");
  });

  it("says why the new-lead job cannot open", () => {
    expect(refusalOf("no-access")).toBe("The CRM is not open to you yet");
    expect(refusalOf("no-feature")).toBe("You do not have access to the CRM");
    expect(refusalOf("off")).toContain("not turned on");
    expect(refusalOf("ready")).toBeNull();
  });

  it("reads no row as admins only", () => {
    expect(adminsOnly(undefined)).toBe(true);
    expect(adminsOnly("admins")).toBe(true);
    expect(adminsOnly("everyone")).toBe(false);
    expect(adminsOnly("groups")).toBe(false);
  });

  it("names a group as group:<slug> and toggles it without a twin", () => {
    expect(groupSubject("sales")).toBe("group:sales");
    expect(toggleGroup([], "group:sales")).toEqual(["group:sales"]);
    expect(toggleGroup(["group:sales"], "group:sales")).toEqual([]);
  });
});
