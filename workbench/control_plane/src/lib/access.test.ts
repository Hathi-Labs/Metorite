/**
 * Fences on the client-side access vocabulary — above all, the ORG-LESS rule.
 *
 * The live defect this file exists for (owner report, 2026-08-24): an
 * org-less sign-in landed on a working-looking home dashboard instead of the
 * "No organization is linked to this email" screen. The gate checked
 * `canSeePath` FIRST, and the home page + floor panes pass `canSeePath` for
 * every authenticated member — so the org-less arm was unreachable on exactly
 * the routes people actually land on. `the floor leaks` below pins that
 * property so nobody "fixes" it by quietly gating the floor; the ordering in
 * `AccessGate.tsx` (org-less before canSeePath) is the other half, advisory
 * because it lives in a component (R7).
 */
import { describe, expect, it } from "vitest";

import { canSeePath, isOrgless, type Access } from "./access";

/** A fully-populated Access with overridable parts — the resolve shape. */
function anAccess(over: Partial<Access> = {}): Access {
  return {
    email: "person@example.com",
    user_id: "u-1",
    authenticated: true,
    is_active: true,
    organization: { id: "o-1", slug: "acme", display_name: "Acme" },
    roles: [],
    legacy_role: "member",
    features: [],
    features_denied: [],
    agents: [],
    permissions: [],
    capabilities: [],
    denied: [],
    is_admin: false,
    ...over,
  };
}

describe("isOrgless — the one definition of a member with no organization", () => {
  it("is true only for an AUTHENTICATED viewer with no org slug", () => {
    expect(isOrgless(anAccess({ organization: {} }))).toBe(true);
    expect(isOrgless(anAccess({ organization: { slug: "" } }))).toBe(true);
  });

  it("is false the moment an organization is resolved", () => {
    expect(isOrgless(anAccess())).toBe(false);
  });

  it("is false for an unauthenticated viewer — that is middleware's redirect, not our card", () => {
    expect(isOrgless(anAccess({ authenticated: false, organization: {} }))).toBe(
      false,
    );
  });
});

describe("the floor leaks: canSeePath alone can NEVER catch an org-less member", () => {
  it("passes the home page and every floor pane for a member with no features and no org", () => {
    // This is the defect's mechanism, pinned as a PROPERTY: these routes are
    // deliberately visible to every authenticated member (the floor), so any
    // gate that consults canSeePath before the org-less check falls through
    // to a working-looking dashboard. If this test ever fails, someone gated
    // the floor — which breaks "My Access" for exactly the person it exists
    // for. The fix for org-less lives in AccessGate's ordering, never here.
    const orgless = anAccess({ organization: {} });
    for (const path of ["/", "/people/me", "/access", "/settings/appearance"]) {
      expect(canSeePath(orgless, path), path).toBe(true);
    }
    expect(isOrgless(orgless)).toBe(true);
  });
});
