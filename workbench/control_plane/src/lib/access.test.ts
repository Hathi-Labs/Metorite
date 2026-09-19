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

import {
  canSeePath,
  isOrgless,
  shouldPollWorkspace,
  type Access,
} from "./access";

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

/**
 * ── The polls behind the hidden sidebar (H-115, measured 2026-09-18)
 *
 * ⚠️ **Hiding a component does not stop its effects.** `Sidebar` returns `null`
 * for an org-less person, after every hook — the Rules of Hooks leave nowhere
 * else to put it — and its two `setInterval(…, 60_000)` polls kept firing at a
 * tenant-scoped API for a person with no tenant.
 *
 * On the box that read as :23 seconds past every minute, 60 an hour for nine
 * hours overnight, each one a 500 with a full traceback in the production log.
 * It was first diagnosed as scattered user traffic across unrelated surfaces.
 * It was one hidden sidebar in one open tab.
 */
describe("shouldPollWorkspace", () => {
  it("REFUSES to poll for a settled org-less member", () => {
    // The whole defect, in one line.
    expect(shouldPollWorkspace(anAccess({ organization: {} }), false)).toBe(false);
  });

  it("polls for an ordinary member of an organization", () => {
    expect(shouldPollWorkspace(anAccess(), false)).toBe(true);
  });

  it("⚠️ polls WHILE LOADING, because nobody knows yet", () => {
    // "Not known to be org-less", never "known to be in an org". Refusing here
    // would cost every ordinary sign-in its first minute of badge updates to a
    // state that is about to say yes. One request against an empty resolve is
    // a request; a 60-second interval against a settled one is the defect.
    expect(shouldPollWorkspace(anAccess({ organization: {} }), true)).toBe(true);
  });

  it("keeps polling for an unauthenticated viewer, deliberately", () => {
    // `/signin` takes AppShell's chromeless branch, so this shell never mounts
    // for them. Widening the test to cover them would also stop polling during
    // a non-authoritative resolve (LS-5), which is a working session.
    expect(
      shouldPollWorkspace(anAccess({ authenticated: false, organization: {} }), false),
    ).toBe(true);
  });

  it("is the SAME answer the sidebar renders on", () => {
    // `Sidebar` reads this function for both its polls and its `return null`.
    // They were two separate conditions, and the polls had none — which is the
    // exact shape of the bug. This pins that they cannot diverge again: every
    // case where we refuse to poll is a case where the sidebar is hidden.
    for (const [access, loading] of [
      [anAccess(), false],
      [anAccess({ organization: {} }), false],
      [anAccess({ organization: {} }), true],
      [anAccess({ authenticated: false, organization: {} }), false],
    ] as Array<[Access, boolean]>) {
      const hidden = !loading && isOrgless(access);
      expect(shouldPollWorkspace(access, loading)).toBe(!hidden);
    }
  });
});
