/**
 * WS-42 (D81) — Projects settings, one pane in two scopes.
 * Spec: project-docs/specs/projects_settings.md §2, §6.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { SPACE_SECTIONS } from "../components/ProjectsSettings";
import { PROJECT_APP_SECTIONS, type ProjectAppId } from "./projectApps";

const components = join(__dirname, "..", "components");
const read = (file: string) => readFileSync(join(components, file), "utf-8");
const MANAGERS = [
  "StatusManager.tsx",
  "FieldManager.tsx",
  "TagManager.tsx",
  "LifecyclePolicy.tsx",
  "SpaceSettings.tsx",
];

describe("one body per setting (D81.3)", () => {
  it.each(MANAGERS)("%s draws through ManagerFrame, and never a Modal of its own", (file) => {
    const src = read(file);
    expect(src).toContain('from "./ManagerFrame"');
    expect(src).toMatch(/<ManagerFrame\b/);
    expect(src).not.toMatch(/from "@\/components\/ui\/Modal"/);
    expect(src).not.toMatch(/fixed inset-0/);
  });
  it("the pane renders the same managers, inline", () => {
    const pane = read("ProjectsSettings.tsx");
    for (const name of ["StatusManager", "FieldManager", "TagManager", "LifecyclePolicy", "SpaceSettings"]) {
      expect(pane).toMatch(new RegExp(`<${name}\\b[\\s\\S]*?\\binline\\b`));
    }
  });
});

describe("a pane inside Projects, never a nav entry (D81.1)", () => {
  it("is a live Projects app", () => {
    const id: ProjectAppId = "settings";
    const item = PROJECT_APP_SECTIONS.flatMap((s) => s.items).find((i) => i.id === id);
    expect(item?.launch).toBe("live");
    expect(item?.label).toBe("Settings");
  });
  it("adds no route and no PANES entry", () => {
    const nav = readFileSync(join(__dirname, "..", "..", "..", "lib", "nav.ts"), "utf-8");
    expect(nav).not.toMatch(/projects\/settings/);
  });
});

describe("the space scope is named, and holds the five sections", () => {
  it("lists the sections in the spec's order", () => {
    expect(SPACE_SECTIONS.map((s) => s.id)).toEqual(["general", "statuses", "fields", "tags", "lifecycle"]);
  });
  it("says how roll-ups read the statuses, since no roll-up setting exists", () => {
    const statuses = SPACE_SECTIONS.find((s) => s.id === "statuses");
    expect(statuses?.hint).toMatch(/roll-ups/);
  });
});

describe("the import lives in settings (D81.5)", () => {
  const page = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");
  it("has no sidebar row of its own", () => {
    expect(page).not.toContain('<span className="min-w-0 flex-1 truncate">Import from ClickUp</span>');
  });
  it("opens from the settings pane and from the empty tree", () => {
    expect(read("ProjectsSettings.tsx")).toContain("Import from ClickUp");
    expect(page).toContain("Import from another tool");
  });
  it("the header menu of a space leads to its settings", () => {
    expect(page).toContain("All settings for this space");
  });
});

describe("the review's findings stay fixed", () => {
  const page = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");
  it("the palette opens it", async () => {
    const { COMMANDS } = await import("./commands");
    const cmd = COMMANDS.find((c) => c.id === "projects.settings");
    expect(cmd?.label).toBe("Projects settings");
  });
  it("a change re-reads the board's fields and tags, not only its lanes", () => {
    const pane = page.slice(page.indexOf("onBoardStale={() => {"), page.indexOf("onBoardStale={() => {") + 200);
    expect(pane).toContain("setTreeKey");
    expect(pane).toContain("loadProject");
  });
  it("the sidebar row starts afresh, at the selected space", () => {
    expect(page).toContain("onApp={chooseApp}");
    expect(page).not.toContain("onApp={setApp}");
  });
});
