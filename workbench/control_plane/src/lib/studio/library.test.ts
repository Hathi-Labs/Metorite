// What the Studio library shows to one member (STU-J1-1, fix round 1).
//
// The page gate is `feature:studio`. The Workflows pill, the Workflows section
// and the Module Studio tab call `/api/workflows`, which needs
// `feature:workflows`. So a member without that feature sees none of the
// three, as a member without `build.apps` sees no Apps.
//
// Mutation: in `library.ts`, set `canWorkflows` to `true`. The tests of the
// member with no workflows feature go red.
//
// STU-Q15a: the Module Studio tab also needs `moduleRuns`, the server's word
// that `MODULE_RUN_ORGS` holds the member's org. A code module runs on the
// box, which is not a sandbox, so the switch ships off. Mutation: in
// `library.ts`, set `canModules` to `canWorkflows`. The tests of the org with
// the switch off go red.
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { NO_ACCESS, type Access } from "@/lib/access";
import { studioLibraryView } from "./library";

function member(features: string[]): Access {
  return { ...NO_ACCESS, authenticated: true, is_active: true, features };
}

const EVERY = member(["studio", "workflows", "build.apps"]);
const NO_WORKFLOWS = member(["studio", "build.apps"]);
const STUDIO_ONLY = member(["studio"]);

describe("the Studio library of a member who holds workflows", () => {
  it("shows the Workflows pill, the section and the Module Studio tab", () => {
    const v = studioLibraryView(EVERY, null, null, true);
    expect(v.kindItems.map((k) => k.id)).toEqual(["all", "apps", "workflows", "skills"]);
    expect(v.tabs.map((t) => t.id)).toEqual(["library", "modules"]);
    expect([v.showApps, v.showWorkflows, v.showSkills]).toEqual([true, true, true]);
  });

  it("opens Module Studio and the Workflows kind from the URL", () => {
    expect(studioLibraryView(EVERY, null, "modules", true).tab).toBe("modules");
    const v = studioLibraryView(EVERY, "workflows", null, true);
    expect([v.activeKind, v.showApps, v.showWorkflows, v.showSkills])
      .toEqual(["workflows", false, true, false]);
  });
});

describe("the Studio library of a member with no workflows feature", () => {
  it("shows no Workflows pill, no Workflows section and no tab row", () => {
    const v = studioLibraryView(NO_WORKFLOWS, null, null, true);
    expect(v.kindItems.map((k) => k.id)).toEqual(["all", "apps", "skills"]);
    expect(v.showWorkflows).toBe(false);
    expect(v.tabs).toEqual([]);
    expect([v.showApps, v.showSkills]).toEqual([true, true]);
  });

  it("falls back to the library for ?tab=modules and to All for ?kind=workflows", () => {
    expect(studioLibraryView(NO_WORKFLOWS, null, "modules", true).tab).toBe("library");
    const v = studioLibraryView(NO_WORKFLOWS, "workflows", null, true);
    expect([v.activeKind, v.showWorkflows, v.showSkills]).toEqual(["all", false, true]);
  });

  it("with only studio, shows the Skills section and nothing that needs another feature", () => {
    const v = studioLibraryView(STUDIO_ONLY, null, null, true);
    expect(v.kindItems.map((k) => k.id)).toEqual(["all", "skills"]);
    expect([v.showApps, v.showWorkflows, v.showSkills]).toEqual([false, false, true]);
    expect(v.tabs).toEqual([]);
  });
});

describe("the Studio library of an org with module runs off (STU-Q15a)", () => {
  it("shows no Module Studio tab, and so no tab row, to a member who holds workflows", () => {
    const v = studioLibraryView(EVERY, null, null, false);
    expect(v.tabs).toEqual([]);
    expect(v.tab).toBe("library");
  });

  it("falls back to the library for ?tab=modules", () => {
    expect(studioLibraryView(EVERY, null, "modules", false).tab).toBe("library");
  });

  it("keeps the Workflows pill and section, which run no code", () => {
    const v = studioLibraryView(EVERY, "workflows", null, false);
    expect(v.kindItems.map((k) => k.id)).toEqual(["all", "apps", "workflows", "skills"]);
    expect([v.activeKind, v.showWorkflows]).toEqual(["workflows", true]);
  });

  it("the switch never opens the tab to a member with no workflows feature", () => {
    expect(studioLibraryView(NO_WORKFLOWS, null, "modules", true).tab).toBe("library");
    expect(studioLibraryView(NO_WORKFLOWS, null, null, true).tabs).toEqual([]);
  });
});

describe("the page reads the one view", () => {
  it("page.tsx takes its pills, tabs and sections from studioLibraryView", () => {
    const page = readFileSync(path.join(__dirname, "../../app/studio/page.tsx"), "utf-8");
    expect(page).toContain("studioLibraryView(access,");
    // The switch comes from the one shell read, never a fetch of its own.
    expect(page).toContain("const moduleRuns = useModuleRunsOn();");
    expect(page).toContain(', moduleRuns);');
    expect(page).not.toContain('{ id: "workflows", label: "Workflows" }');
    expect(page).not.toContain('{ id: "modules", label: "Module Studio"');
  });
});
