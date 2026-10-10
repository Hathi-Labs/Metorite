/**
 * Org-aware links (MT-1k, owner 2026-10-10). R7 fence `org-link-decision`:
 * what a page does with `?org=`, and the paths a switch or a sign-in may land
 * on. Each case is written so the obvious wrong build fails it.
 */
import { describe, expect, it } from "vitest";

import type { Accounts } from "./accountSwitch";
import {
  decideOrgLink,
  isOrgId,
  safeLocalPath,
  signInUrl,
  withOrg,
  withoutOrg,
} from "./orgLink";

const ORG_A = "3f2a6c1e-9b7d-4e2a-8c1f-0a1b2c3d4e5f";
const ORG_B = "7c9d1e2f-3a4b-4c5d-8e6f-112233445566";
const ORG_C = "00000000-1111-4222-8333-444444444444";

function accounts(enabled: boolean, others: Array<{ slot: number; email: string; org: string | null }>): Accounts {
  return {
    enabled,
    active: { email: "a@one.test", name: "a", organization_id: ORG_A },
    others: others.map((o) => ({
      slot: o.slot,
      email: o.email,
      name: null,
      organization: null,
      organization_id: o.org,
    })),
  };
}

const base = { authenticated: true, activeOrgId: ORG_A, accounts: null };

describe("decideOrgLink", () => {
  it("does nothing without an org parameter", () => {
    expect(decideOrgLink({ ...base, param: null })).toEqual({ kind: "none" });
  });

  it("strips a value that is not a uuid, before any read", () => {
    for (const bad of ["", "acme", "1", `${ORG_A}x`, "../x", `${ORG_A.slice(0, -1)}`]) {
      expect(decideOrgLink({ ...base, param: bad }), bad).toEqual({ kind: "strip" });
    }
  });

  it("strips the active account's own org, in any case, with no read", () => {
    expect(decideOrgLink({ ...base, param: ORG_A })).toEqual({ kind: "strip" });
    expect(decideOrgLink({ ...base, param: ORG_A.toUpperCase() })).toEqual({ kind: "strip" });
  });

  it("leaves a signed-out viewer to the proxy, and keeps the parameter", () => {
    expect(decideOrgLink({ ...base, authenticated: false, param: ORG_B })).toEqual({ kind: "none" });
  });

  it("reads the other accounts before it decides on another org", () => {
    expect(decideOrgLink({ ...base, param: ORG_B })).toEqual({ kind: "load" });
    // An org-less account has no org to match, so it reads too.
    expect(decideOrgLink({ ...base, activeOrgId: undefined, param: ORG_B })).toEqual({ kind: "load" });
  });

  it("switches to the signed-in account of that org, and to no other", () => {
    const a = accounts(true, [
      { slot: 0, email: "c@three.test", org: ORG_C },
      { slot: 2, email: "b@two.test", org: ORG_B },
    ]);
    expect(decideOrgLink({ ...base, param: ORG_B, accounts: a })).toEqual({
      kind: "switch",
      slot: 2,
      email: "b@two.test",
    });
  });

  it("shows the notice, with the stash, when no signed-in account matches", () => {
    const a = accounts(true, [
      { slot: 0, email: "c@three.test", org: ORG_C },
      { slot: 1, email: "d@four.test", org: null },
    ]);
    expect(decideOrgLink({ ...base, param: ORG_B, accounts: a })).toEqual({ kind: "notice", canAdd: true });
  });

  it("never switches while the switcher is off, and offers no stash", () => {
    const a = accounts(false, [{ slot: 2, email: "b@two.test", org: ORG_B }]);
    expect(decideOrgLink({ ...base, param: ORG_B, accounts: a })).toEqual({ kind: "notice", canAdd: false });
  });
});

describe("safeLocalPath", () => {
  it("accepts a same-origin relative path with its query and hash", () => {
    expect(safeLocalPath("/projects?task=1")).toBe("/projects?task=1");
    expect(safeLocalPath("/")).toBe("/");
    expect(safeLocalPath("/email?email=e1&account=m1#x")).toBe("/email?email=e1&account=m1#x");
  });

  it("refuses every shape that leaves the site", () => {
    for (const bad of [
      "//evil.example",
      "//evil.example/projects",
      "/\\evil.example",
      "\\\\evil.example",
      "/\t/evil.example",
      "/\n/evil.example",
      "https://evil.example",
      "http://x",
      "javascript:alert(1)",
      "projects",
      "",
      null,
      undefined,
      42,
    ]) {
      expect(safeLocalPath(bad), String(bad)).toBeNull();
    }
  });
});

describe("the link shapes", () => {
  it("withoutOrg drops org and keeps every other parameter, in order", () => {
    expect(withoutOrg(`/projects?task=t1&org=${ORG_B}&view=board#c`)).toBe("/projects?task=t1&view=board#c");
    expect(withoutOrg(`/projects?org=${ORG_B}`)).toBe("/projects");
  });

  it("withOrg stamps a uuid and ignores anything else", () => {
    expect(withOrg("https://app.example/projects?task=t1", ORG_A)).toBe(
      `https://app.example/projects?task=t1&org=${ORG_A}`,
    );
    expect(withOrg("/projects", ORG_A)).toBe(`/projects?org=${ORG_A}`);
    expect(withOrg("/projects?task=t1#c", ORG_A)).toBe(`/projects?task=t1&org=${ORG_A}#c`);
    expect(withOrg("/projects?task=t1", null)).toBe("/projects?task=t1");
    expect(withOrg("/projects?task=t1", "acme")).toBe("/projects?task=t1");
  });

  it("signInUrl comes back to the whole link, and never off the site", () => {
    const link = `/projects?task=t1&org=${ORG_B}`;
    const url = new URL(signInUrl(link, true), "https://app.example");
    expect(url.pathname).toBe("/signin");
    expect(url.searchParams.get("add")).toBe("1");
    expect(url.searchParams.get("callbackUrl")).toBe(link);
    expect(new URL(signInUrl(link, false), "https://app.example").searchParams.get("add")).toBeNull();
    expect(signInUrl("//evil.example", false)).toBe("/signin");
  });

  it("isOrgId is the uuid shape", () => {
    expect(isOrgId(ORG_A)).toBe(true);
    expect(isOrgId(ORG_A.toUpperCase())).toBe(true);
    expect(isOrgId("not-a-uuid")).toBe(false);
    expect(isOrgId(undefined)).toBe(false);
  });
});
