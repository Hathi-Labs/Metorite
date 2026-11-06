// What the Studio library shows to one member (STU-J1-1, fix round 1).
//
// The page gate is `feature:studio`. The Workflows pill, the Workflows section
// and the Module Studio tab call `/api/workflows`, which needs
// `feature:workflows`. So a member without that feature sees none of the
// three, as a member without `build.apps` sees no Apps.
//
// Mutation: in `library.ts`, set `canWorkflows` to `true`. The tests of the
// member with no workflows feature go red.
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
    const v = studioLibraryView(EVERY, null, null);
    expect(v.kindItems.map((k) => k.id)).toEqual(["all", "apps", "workflows", "skills"]);
    expect(v.tabs.map((t) => t.id)).toEqual(["library", "modules"]);
    expect([v.showApps, v.showWorkflows, v.showSkills]).toEqual([true, true, true]);
  });

  it("opens Module Studio and the Workflows kind from the URL", () => {
    expect(studioLibraryView(EVERY, null, "modules").tab).toBe("modules");
    const v = studioLibraryView(EVERY, "workflows", null);
    expect([v.activeKind, v.showApps, v.showWorkflows, v.showSkills])
      .toEqual(["workflows", false, true, false]);
  });
});

describe("the Studio library of a member with no workflows feature", () => {
  it("shows no Workflows pill, no Workflows section and no tab row", () => {
    const v = studioLibraryView(NO_WORKFLOWS, null, null);
    expect(v.kindItems.map((k) => k.id)).toEqual(["all", "apps", "skills"]);
    expect(v.showWorkflows).toBe(false);
    expect(v.tabs).toEqual([]);
    expect([v.showApps, v.showSkills]).toEqual([true, true]);
  });

  it("falls back to the library for ?tab=modules and to All for ?kind=workflows", () => {
    expect(studioLibraryView(NO_WORKFLOWS, null, "modules").tab).toBe("library");
    const v = studioLibraryView(NO_WORKFLOWS, "workflows", null);
    expect([v.activeKind, v.showWorkflows, v.showSkills]).toEqual(["all", false, true]);
  });

  it("with only studio, shows the Skills section and nothing that needs another feature", () => {
    const v = studioLibraryView(STUDIO_ONLY, null, null);
    expect(v.kindItems.map((k) => k.id)).toEqual(["all", "skills"]);
    expect([v.showApps, v.showWorkflows, v.showSkills]).toEqual([false, false, true]);
    expect(v.tabs).toEqual([]);
  });
});

describe("the page reads the one view", () => {
  it("page.tsx takes its pills, tabs and sections from studioLibraryView", () => {
    const page = readFileSync(path.join(__dirname, "../../app/studio/page.tsx"), "utf-8");
    expect(page).toContain("studioLibraryView(access,");
    expect(page).not.toContain('{ id: "workflows", label: "Workflows" }');
    expect(page).not.toContain('{ id: "modules", label: "Module Studio"');
  });
});
