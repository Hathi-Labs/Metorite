import { describe, expect, it } from "vitest";

import { adminsOnly, groupSubject, isAccessDenied, toggleGroup } from "./access";
import { CrmError } from "./api";
import { firstOpen } from "./connect";

describe("the CRM access refusal (CRM-L2)", () => {
  it("reads a 403 of the gateway as the access rule, and nothing else", () => {
    expect(isAccessDenied(new CrmError("Forbidden", 403))).toBe(true);
    expect(isAccessDenied(new CrmError("Not Found", 404))).toBe(false);
    expect(isAccessDenied(new Error("network"))).toBe(false);
  });

  it("draws the no-access state once the read is done, and a skeleton before", () => {
    const base = { connection: null, settingsMode: undefined, isAdmin: false };
    expect(firstOpen({ ...base, loading: false, denied: true })).toBe("no-access");
    expect(firstOpen({ ...base, loading: true, denied: true })).toBe("loading");
    expect(firstOpen({ ...base, loading: false })).toBe("ready");
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
