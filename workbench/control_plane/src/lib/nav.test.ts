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

import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { JOBS } from "@/lib/shell/registry";
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
  // Added 2026-08-24 by D54 (board WS-39 S2), taking the live set from eight to
  // NINE. Calendar was lifted out of `/tasks`, where it was a view mode; it is
  // `live` rather than `preview` because it ships today inside a live app, so
  // holding it back would withdraw a capability customers already have.
  // This edit is the fence doing its job — the pane could not land without
  // somebody deliberately changing this line AND `launch_surface.md` §2.
  ["personal", "/calendar"],
  ["personal", "/people/me"],
  // `["personal", "/access"]` was here until 2026-10-05. The owner moved My
  // Access out of the sidebar and into the People app as a tab, which took
  // the live set from ELEVEN back to TEN.
  // Added 2026-10-02 by owner decision of 2026-10-01 (H-21, WS-17 EM-T3b),
  // taking the live set from TEN to ELEVEN. Email sits after `/people/me`
  // because `/dashboard` between them is still `preview`.
  ["personal", "/email"],
  // Added 2026-10-08 by owner decision (WS-20 WA-C6), taking the live set from
  // TEN to ELEVEN. "My WhatsApp" sits after "My Email", in nav order.
  ["personal", "/whatsapp"],
  ["apps", "/projects"],
  // Added 2026-09-20 by owner decision, taking the live set from NINE to TEN.
  // The directory was held back while it could not load at all (PR #306) and
  // while it had no rows to show (H-124's roster sync). Same fence, same
  // rule: this line and `launch_surface.md` §2 move together or the suite
  // goes red.
  ["apps", "/people"],
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
    expect(CHROMELESS_ROUTES).toEqual(["/signin", "/signup", "/oauth/approved"]);
    expect(isChromeless("/signup")).toBe(true);
    expect(isChromeless("/signin")).toBe(true);
    expect(isChromeless("/signin/code")).toBe(true);
  });

  it("the admin-consent landing page has no chrome (WS-17 EM-T3c)", () => {
    // An IT admin with no Metorite session lands here. The sidebar would say
    // "you are inside a workspace", and that admin is not.
    expect(isChromeless("/oauth/approved")).toBe(true);
    expect(isChromeless("/oauth")).toBe(false);
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
  it("ships exactly the eleven panes launch_surface.md §2 names", () => {
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

  it("calls the personal lens My Tasks — D73, the mirror of launch_surface.md §2", () => {
    const pane = panesWithSection().find(([, p]) => p.href === "/tasks");
    expect(pane?.[1].label).toBe("My Tasks");
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
    // The example is Notes since 2026-10-08: WhatsApp went live (WA-C6), as
    // Email did before it (EM-T3b).
    const withNotesGrant = visibleSections(["notes", "chat"], false, false);
    expect(withNotesGrant.flatMap((s) => s.items.map((p) => p.href))).not.toContain(
      "/notes",
    );

    const withoutChat = visibleSections(["notes"], false, false);
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
    // The ungated live panes are the floor: My Profile and Appearance. My
    // Access is a People tab since 2026-10-05, and not a pane.
    const shown = visibleSections([], false).flatMap((s) =>
      s.items.map((p) => p.href),
    );
    expect(shown).toEqual(["/people/me", "/settings/appearance"]);
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

// ── The shell manifest (D89, `navigation_shell.md` §5.1, NS-2) ─────────────

describe("every live pane carries its manifest (D89)", () => {
  it.each(LIVE_PANES.map((p) => [p.href, p] as const))("%s has a team", (_href, p) => {
    expect(["personal", "across", "studio", "admin"]).toContain(p.team);
  });

  it.each(LIVE_PANES.map((p) => [p.href, p] as const))(
    "%s says what it is for, in one line of 60 characters or fewer",
    (_href, p) => {
      expect(p.blurb, "the launcher and the command bar print this").toBeTruthy();
      expect(p.blurb!.length).toBeLessThanOrEqual(60);
      // One sentence, in words a member uses: no dot-separated jargon list,
      // no trailing full stop, and not the operator line again.
      expect(p.blurb).not.toMatch(/·|\.$/);
      expect(p.blurb).not.toBe(p.note);
    },
  );

  it("gives every pane a team, live or not, so a promoted app needs no second edit", () => {
    expect(PANES.filter((p) => !p.team).map((p) => p.href)).toEqual([]);
  });

  it("puts only pages about the member in the account menu", () => {
    const account = PANES.filter((p) => p.door === "account").map((p) => p.href);
    expect(account).toEqual(["/people/me", "/settings/appearance"]);
  });

  it("gives Organisation a sidebar door in Admin, as an app (owner, 2026-10-09)", () => {
    const org = PANES.find((p) => p.href === "/settings/organization")!;
    expect(org.team).toBe("admin");
    expect(org.door ?? "sidebar").toBe("sidebar");
    expect(org.adminOnly).toBe(true);
    expect(org.setting).toBeUndefined();
  });

  it("marks only preferences as settings, so All apps lists apps", () => {
    expect(PANES.filter((p) => p.setting).map((p) => p.href)).toEqual([
      "/people/me",
      "/settings/appearance",
    ]);
    // A setting is never a sidebar door: it has no place to show then.
    expect(PANES.filter((p) => p.setting && p.door !== "account")).toEqual([]);
  });
});

// ── Done-when 2 of NS-2: a job opens a real page (`navigation_shell.md`) ───

const APP_DIR = path.join(__dirname, "..", "app");

/**
 * Whether `src/app` holds a `page.tsx` for this path. A route group, such as
 * `(auth)`, adds no segment. A dynamic folder, such as `[id]`, takes any one
 * segment, and a catch-all folder takes the rest.
 */
function routeHasPage(pathname: string): boolean {
  const walk = (dir: string, segs: readonly string[]): boolean => {
    if (segs.length === 0 && existsSync(path.join(dir, "page.tsx"))) return true;
    for (const name of readdirSync(dir)) {
      const child = path.join(dir, name);
      if (!statSync(child).isDirectory() || name === "api") continue;
      if (/^\(.+\)$/.test(name) && walk(child, segs)) return true;
      if (segs.length === 0) continue;
      if (/^\[\[?\.\.\..+\]\]?$/.test(name) && existsSync(path.join(child, "page.tsx"))) return true;
      if ((name === segs[0] || /^\[[^.[\]]+\]$/.test(name)) && walk(child, segs.slice(1))) return true;
    }
    return false;
  };
  return walk(APP_DIR, pathname.split("/").filter(Boolean));
}

describe("every job opens a page that exists (NS-2 done-when 2)", () => {
  it("the check refuses a path with no page, so the fence below is not empty", () => {
    expect(routeHasPage("/nope")).toBe(false);
    expect(routeHasPage("/settings/nope")).toBe(false);
    expect(routeHasPage("/tasks")).toBe(true);
    // A dynamic segment: `settings/members/[email]`.
    expect(routeHasPage("/settings/members/someone@x.test")).toBe(true);
  });

  it.each(JOBS.map((j) => [j.id, j.href] as const))("%s opens %s", (_id, href) => {
    const pathname = new URL(href, "http://metorite.test").pathname;
    expect(routeHasPage(pathname), `${href} has no src/app page`).toBe(true);
  });
});

describe("every live app has a job (NS-2 done-when 1)", () => {
  // A live app with no job is invisible in the command bar's Do group, so a
  // member who types what they want to do there finds only "Open …".
  it.each(LIVE_PANES.map((p) => [p.label, p.href] as const))("%s (%s) has one job or more", (_label, href) => {
    expect(JOBS.some((j) => j.app === href), `${href} has no job in lib/shell/registry.ts`).toBe(true);
  });
});
