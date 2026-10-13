/**
 * OrgLinkGate's arms: what the gate draws for each decision (MT-1k, org-aware
 * links). R7 fence `org-link-gate-arms`.
 *
 * `decideOrgLink` is the rule, and `lib/orgLink.test.ts` holds it. This file
 * holds the wiring. These checks fail on the wrong builds:
 *   1. A read or a switch draws the page instead of the cover. The page
 *      would then mount as the wrong account.
 *   2. The notice draws the page, or loses "Sign in to that account" or
 *      "Stay here".
 *   3. No `org`, or a stripped `org`, draws anything but the page.
 *   4. A real link to another org draws the page before the accounts read.
 * The flow in a browser is `e2e/org-link.spec.ts`.
 *
 * ⚠️ The Suspense fallback is NOT fenced here. It is the page today, and a
 * cold load mounts every page twice through it (HANDOFF H-302).
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";
import type { OrgLinkDecision } from "@/lib/orgLink";

const OTHER_ORG = "22222222-2222-4222-8222-222222222222";
const ACTIVE_ORG = "11111111-1111-4111-8111-111111111111";

/** The `org` value in the address bar for this render. */
const url: { query: string } = { query: "" };
/** A decision to force, or `null` to run the real rule. */
const forced: { decision: OrgLinkDecision | null } = { decision: null };

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(url.query),
}));

vi.mock("@/lib/orgLink", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/orgLink")>();
  return {
    ...real,
    decideOrgLink: (input: Parameters<typeof real.decideOrgLink>[0]) => forced.decision ?? real.decideOrgLink(input),
  };
});

vi.mock("@/components/AccessProvider", () => ({
  useAccess: () => ({
    access: {
      ...NO_ACCESS,
      authenticated: true,
      email: "asha@example.com",
      organization: { id: ACTIVE_ORG, slug: "acme", display_name: "Acme" },
    },
    loading: false,
    stale: false,
    refresh: async () => {},
  }),
}));

vi.mock("@/components/AccountSwitcher", () => ({
  SwitchingCover: ({ email }: { email: string | null }) =>
    createElement("div", { "data-switching-cover": email ?? "" }),
}));

const { default: OrgLinkGate } = await import("@/components/OrgLinkGate");

const PAGE = createElement("section", { "data-page": "" }, "The page");
const draw = () => renderToStaticMarkup(createElement(OrgLinkGate, null, PAGE));

describe("OrgLinkGate's arms", () => {
  beforeEach(() => {
    url.query = "";
    forced.decision = null;
  });

  it("draws the cover and no page while it reads the accounts", () => {
    forced.decision = { kind: "load" };
    const html = draw();
    expect(html).toContain("data-switching-cover");
    expect(html).not.toContain("The page");
  });

  it("draws the cover, with the account's address, and no page during a switch", () => {
    // A switch always has an `org` in the URL. The gate compares it with the
    // value of a refused switch, so the test gives it one.
    url.query = `org=${OTHER_ORG}`;
    forced.decision = { kind: "switch", slot: 2, email: "asha@fracktal.in" };
    const html = draw();
    expect(html).toContain('data-switching-cover="asha@fracktal.in"');
    expect(html).not.toContain("The page");
  });

  it("draws the notice and no page for an org that no signed-in account holds", () => {
    forced.decision = { kind: "notice", canAdd: true };
    const html = draw();
    expect(html).toContain("not signed in to");
    expect(html).toContain("Sign in to that account");
    expect(html).toContain("Stay here");
    expect(html).toContain("This account stays signed in.");
    expect(html).not.toContain("The page");
  });

  it("says the sign-in replaces this session when the switcher is off", () => {
    forced.decision = { kind: "notice", canAdd: false };
    const html = draw();
    expect(html).toContain("This signs you out of this account.");
    expect(html).not.toContain("The page");
  });

  it("draws the page for no org and for a stripped org", () => {
    for (const decision of [{ kind: "none" }, { kind: "strip" }] as const) {
      forced.decision = decision;
      const html = draw();
      expect(html).toContain("The page");
      expect(html).not.toContain("data-switching-cover");
    }
  });

  it("with the real rule: a link to another org draws the cover, not the page", () => {
    url.query = `org=${OTHER_ORG}`;
    const html = draw();
    expect(html).toContain("data-switching-cover");
    expect(html).not.toContain("The page");
  });

  it("with the real rule: the active org and no org draw the page", () => {
    for (const q of [`org=${ACTIVE_ORG}`, ""]) {
      url.query = q;
      expect(draw()).toContain("The page");
    }
  });
});
