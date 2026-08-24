/**
 * The launch surface's fence (R7).
 *
 * Owning spec: `project-docs/specs/launch_surface.md` §2 (the allowlist of
 * record) · §3 (`preview` semantics) · §8.1 (the unresolved-viewer rule) ·
 * tickets LS-1 and LS-4. Decision D49.
 *
 * The point of this file is that **the spec's table and the registry cannot
 * drift apart silently**. `LIVE_SET` below is a transcription of
 * `launch_surface.md` §2's live table; if somebody adds a pane, promotes one,
 * or quietly ships an unfinished app into the customer's sidebar, one of these
 * assertions fails and names what changed.
 */

import { describe, expect, it } from "vitest";

import {
  CHROMELESS_ROUTES,
  isChromeless,
  LIVE_PANES,
  NAV_SECTIONS,
  PANES,
  previewAppsVisible,
  visibleSections,
  type NavPane,
} from "./nav";

/**
 * `launch_surface.md` §2's live table, verbatim, as `(section id, href)`.
 *
 * ⚠️ Changing this array is changing what we sell. It is meant to be a
 * deliberate edit made in the same PR as the spec table, never a fix to make a
 * red test green.
 */
const LIVE_SET: ReadonlyArray<[string, string]> = [
  ["personal", "/tasks"],
  ["personal", "/people/me"],
  ["personal", "/access"],
  ["apps", "/projects"],
  ["ai-studio", "/chat"],
  ["admin", "/approvals"],
  ["admin", "/settings/organization"],
  ["admin", "/settings/appearance"],
];

/** Every pane, paired with the id of the section it sits in. */
function panesWithSection(): Array<[string, NavPane]> {
  return NAV_SECTIONS.flatMap((s) => s.items.map((p): [string, NavPane] => [s.id, p]));
}

/** Every feature slug in the registry — the grant set a full member holds. */
const ALL_FEATURES = PANES.map((p) => p.feature).filter(
  (f): f is string => typeof f === "string",
);

describe("chromeless onboarding routes (CP-2c onboarding UX)", () => {
  it("covers exactly the doorway — sign-in and sign-up, with their subpaths", () => {
    expect(CHROMELESS_ROUTES).toEqual(["/signin", "/signup"]);
    expect(isChromeless("/signup")).toBe(true);
    expect(isChromeless("/signin")).toBe(true);
    expect(isChromeless("/signin/code")).toBe(true);
  });

  it("matches path SEGMENTS, never prefixes of other routes", () => {
    // A route that merely starts with the same letters must keep its chrome —
    // the naive startsWith("/signin") would strip the shell from a future
    // "/signin-help" page.
    expect(isChromeless("/signup-guide")).toBe(false);
    expect(isChromeless("/")).toBe(false);
    expect(isChromeless("/settings/organization")).toBe(false);
  });

  it("no chromeless route is a navigable pane — the doorway is not in the sidebar", () => {
    for (const pane of PANES) {
      expect(isChromeless(pane.href)).toBe(false);
    }
  });
});

describe("the launch allowlist (LS-1)", () => {
  it("ships exactly the eight panes launch_surface.md §2 names", () => {
    const live = panesWithSection()
      .filter(([, p]) => p.launch === "live")
      .map(([section, p]): [string, string] => [section, p.href]);
    expect(live).toEqual(LIVE_SET);
  });

  it("gives every pane an explicit launch status", () => {
    // The type already requires it; this catches a pane cast through `any` or
    // spread from a partial, which is how a required field goes missing in
    // practice.
    for (const p of PANES) {
      expect(["live", "preview"], `${p.href} has no launch status`).toContain(p.launch);
    }
  });

  it("has no Centers section — D49 withdrew the surface", () => {
    for (const s of NAV_SECTIONS) {
      expect(s.id).not.toBe("centers");
      expect(s.label.toLowerCase()).not.toBe("centers");
    }
  });

  it("keeps the Center landing pages in the tree, as preview panes", () => {
    // The other half of D49: withdrawn from the surface, NOT deleted. If these
    // disappear, somebody deleted Center code that the group vocabulary and
    // D12's live Projects grants rest on.
    const centerPanes = PANES.filter((p) => p.href.startsWith("/centers/"));
    expect(centerPanes.length).toBeGreaterThan(0);
    for (const p of centerPanes) expect(p.launch).toBe("preview");
  });

  it("names the four sections D49 settled on, in order", () => {
    expect(NAV_SECTIONS.map((s) => s.id)).toEqual([
      "personal",
      "apps",
      "ai-studio",
      "admin",
    ]);
    expect(NAV_SECTIONS.map((s) => s.label)).toEqual([
      "Personal Center",
      "Apps",
      "AI Studio",
      "Admin",
    ]);
  });
});

describe("preview panes are hidden, and the flag restores them (LS-1)", () => {
  it("shows only live panes to a fully-granted admin with the flag off", () => {
    const sections = visibleSections(ALL_FEATURES, true, false);
    const shown = sections.flatMap((s) => s.items.map((p) => p.href));
    expect(shown).toEqual(LIVE_PANES.map((p) => p.href));
  });

  it("restores every preview pane when the flag is on", () => {
    const sections = visibleSections(ALL_FEATURES, true, true);
    const shown = sections.flatMap((s) => s.items.map((p) => p.href));
    // Every pane whose gate this caller satisfies — which, holding every
    // feature and the admin flag, is all of them.
    expect(shown).toEqual(PANES.map((p) => p.href));
  });

  it("does not treat launch status as a permission", () => {
    // The §3.4 rule, as a test: holding the feature is NOT enough to reveal a
    // preview pane, and lacking it still hides a live one. If these two ever
    // agree, somebody has started hiding apps by revoking grants.
    const withEmailGrant = visibleSections(["email", "chat"], false, false);
    expect(withEmailGrant.flatMap((s) => s.items.map((p) => p.href))).not.toContain(
      "/email",
    );

    const withoutChat = visibleSections(["email"], false, false);
    expect(withoutChat.flatMap((s) => s.items.map((p) => p.href))).not.toContain(
      "/chat",
    );
  });

  it("reads the flag from the environment, and defaults to off", () => {
    expect(previewAppsVisible({})).toBe(false);
    expect(previewAppsVisible({ NEXT_PUBLIC_SHOW_PREVIEW_APPS: "0" })).toBe(false);
    expect(previewAppsVisible({ NEXT_PUBLIC_SHOW_PREVIEW_APPS: "" })).toBe(false);
    expect(previewAppsVisible({ NEXT_PUBLIC_SHOW_PREVIEW_APPS: "1" })).toBe(true);
    expect(previewAppsVisible({ NEXT_PUBLIC_SHOW_PREVIEW_APPS: "true" })).toBe(true);
    expect(previewAppsVisible({ NEXT_PUBLIC_SHOW_PREVIEW_APPS: "on" })).toBe(true);
  });
});

describe("an unresolved viewer sees nothing, never everything (LS-4 · §8.1)", () => {
  it("returns no sections while access is unresolved", () => {
    // This is the whole of the reported "sometimes all the apps appear" bug.
    // The old behaviour returned NAV_SECTIONS here, so first paint showed the
    // complete application and then shrank. If this assertion is ever
    // loosened, that flash is back.
    expect(visibleSections(null)).toEqual([]);
    expect(visibleSections(null, true)).toEqual([]);
    expect(visibleSections(null, true, true)).toEqual([]);
  });

  it("returns no sections for a resolved member who holds nothing", () => {
    // The ungated live panes are the floor: My Profile, My Access, Appearance.
    const shown = visibleSections([], false).flatMap((s) =>
      s.items.map((p) => p.href),
    );
    expect(shown).toEqual(["/people/me", "/access", "/settings/appearance"]);
  });

  it("hides the admin-only Organisation pane from a non-admin", () => {
    const nonAdmin = visibleSections(ALL_FEATURES, false).flatMap((s) =>
      s.items.map((p) => p.href),
    );
    expect(nonAdmin).not.toContain("/settings/organization");

    const admin = visibleSections(ALL_FEATURES, true).flatMap((s) =>
      s.items.map((p) => p.href),
    );
    expect(admin).toContain("/settings/organization");
  });

  it("drops a section once every pane in it is filtered away", () => {
    // A member with no chat grant should not be shown an empty "AI Studio"
    // heading.
    const sections = visibleSections(["tasks"], false);
    expect(sections.map((s) => s.id)).not.toContain("ai-studio");
  });
});
