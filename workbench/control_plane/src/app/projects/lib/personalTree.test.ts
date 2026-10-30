/**
 * The Personal space in the Projects sidebar (WS-64 OP-3, D-OP-2).
 *
 * Pure assertions on the flag gate, the lookup tree, and the row menu of a
 * personal node. The e2e case `e2e/projects-my-work.spec.ts` draws the section.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import type { ProjectRow } from "./api";
import {
  PERSONAL_SECTION_LABEL,
  displayNodeName,
  displayProjectName,
  displayRowProject,
  rememberPersonalRoot,
  firstTeamSpaceId,
  personalEmptyLine,
  personalHeading,
  personalPlaceCount,
  lookupRoots,
  moveDestinationRoots,
  personalRoots,
  splitPersonal,
} from "./personalTree";
import { emptyCopy } from "./dashboardRows";
import { openCount as overviewOpen } from "./myTasks";
import { openCount as sidebarOpen } from "./progressWheel";
import { projectMenuItems } from "./projectMenu";
import { nodeLevel, nodeKind, pathTo } from "./tree";

const node = (id: string, over: Partial<ProjectRow> = {}): ProjectRow => ({
  id,
  name: id,
  ...over,
});

const personalArea = node("area", {
  personal: true,
  kind: "folder",
  parent_project_id: "root",
  can_delete: true,
  can_write_grants: false,
  grant_refusal: "A personal node is private.",
});
const personalRoot = node("root", {
  personal: true,
  can_delete: false,
  can_write_grants: false,
  children: [personalArea],
});
// Migration 297 keeps a TEAM node under the caller's root. It stays in `rows`.
// The server (`tree.py` `_nest`) makes it a ROOT of `rows`.
const teamKept = node("team-kept", { parent_project_id: "root" });
const teamSpace = node("space");
const data = { rows: [teamSpace, teamKept], personal: [personalRoot] };

const handlers = () => ({
  onSetState: vi.fn(),
  onArchive: vi.fn(),
  onUnarchive: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
});
const labels = (items: ReturnType<typeof projectMenuItems>) =>
  items.filter((i) => i.kind === "item").map((i) => (i as { label: string }).label);

describe("personalRoots (the flag)", () => {
  it("is the caller's tree with the flag on", () => {
    expect(personalRoots(data, true)).toEqual([{ ...personalRoot, name: "Personal" }]);
  });
  it("is empty with the flag off, so nothing is drawn", () => {
    expect(personalRoots(data, false)).toEqual([]);
  });
  it("is empty for an older gateway with no personal key", () => {
    expect(personalRoots({}, true)).toEqual([]);
    expect(personalRoots(undefined, true)).toEqual([]);
  });
  it("names the section Personal", () => {
    expect(PERSONAL_SECTION_LABEL).toBe("Personal");
  });
});

describe("lookupRoots", () => {
  it("equals the team rows when there is no personal tree (flag off)", () => {
    const rows = data.rows;
    expect(lookupRoots(rows, [])).toBe(rows);
  });
  it("finds a node two levels under the personal root", () => {
    const all = lookupRoots(data.rows, data.personal);
    expect(pathTo(all, "area").map((n) => n.id)).toEqual(["root", "area"]);
  });
  it("keeps a team node under the caller's root in Spaces, not in Personal", () => {
    expect(pathTo(data.rows, "team-kept").map((n) => n.id)).toEqual(["team-kept"]);
    expect(data.rows.map((n) => n.id)).toContain("team-kept");
    expect(pathTo(personalRoots(data, true), "team-kept")).toEqual([]);
  });
});

describe("the row menu of a personal node", () => {
  const ui = {
    onBeginRename: vi.fn(),
    onMove: vi.fn(),
    onCreate: vi.fn(),
    createOptions: [{ kind: "folder" as const, label: "New folder", level: "folder" as const }],
  };

  it("offers no share, access or grant item, and no New space", () => {
    for (const row of [personalRoot, personalArea]) {
      const level = nodeLevel(nodeKind(row), row === personalRoot ? 1 : 1);
      const got = labels(projectMenuItems(row, handlers(), ui, level));
      expect(got.join("|")).not.toMatch(/share|access|grant|new space/i);
    }
  });

  it("offers Move on an Area but not on the personal root", () => {
    expect(labels(projectMenuItems(personalArea, handlers(), ui, "folder"))).toContain("Move to…");
    expect(labels(projectMenuItems(personalRoot, handlers(), ui, "space"))).not.toContain("Move to…");
  });

  it("does not archive the personal root, but archives an Area", () => {
    expect(labels(projectMenuItems(personalRoot, handlers(), ui, "space"))).not.toContain("Archive");
    expect(labels(projectMenuItems(personalArea, handlers(), ui, "folder"))).toContain("Archive");
  });

  it("follows can_delete for Delete", () => {
    expect(labels(projectMenuItems(personalRoot, handlers(), ui, "space"))).not.toContain("Delete");
    expect(labels(projectMenuItems(personalArea, handlers(), ui, "folder"))).toContain("Delete");
  });

  it("leaves a team space menu as it was", () => {
    const got = labels(projectMenuItems(teamSpace, handlers(), ui, "space"));
    expect(got).toContain("Move to…");
    expect(got).toContain("Archive");
  });
});

describe("an archived personal Area (fix round 1)", () => {
  const archivedArea = node("area", { personal: true, archived_at: "2026-10-01T00:00:00Z" });
  const liveArea = node("live-area", { personal: true });
  const root = node("root", { personal: true, children: [archivedArea, liveArea] });
  it("leaves the live Personal tree and heads the Personal archived fold", () => {
    const split = splitPersonal([root]);
    expect(split.active[0].children?.map((n) => n.id)).toEqual(["live-area"]);
    expect(split.archived.map((e) => e.node.id)).toEqual(["area"]);
    expect(split.count).toBe(1);
  });
  it("comes back when unarchived", () => {
    const back = { ...root, children: [{ ...archivedArea, archived_at: null }, liveArea] };
    const split = splitPersonal([back]);
    expect(split.active[0].children?.map((n) => n.id)).toEqual(["area", "live-area"]);
    expect(split.count).toBe(0);
  });
});

describe("moveDestinationRoots (fix round 1)", () => {
  const team = [teamSpace, teamKept];
  const mine = [personalRoot];
  it("offers the team tree for team tasks", () => {
    expect(moveDestinationRoots(["space", "team-kept"], team, mine)).toEqual({ roots: team, mixed: false });
  });
  it("offers the personal tree for personal tasks", () => {
    expect(moveDestinationRoots(["area", "root"], team, mine)).toEqual({ roots: mine, mixed: false });
  });
  it("offers nothing for a mixed selection", () => {
    expect(moveDestinationRoots(["space", "area"], team, mine)).toEqual({ roots: [], mixed: true });
  });
});

describe("firstTeamSpaceId (fix round 1)", () => {
  it("skips the personal root", () => {
    expect(firstTeamSpaceId([personalRoot, teamSpace])).toBe("space");
  });
  it("is null for a member with no team space", () => {
    expect(firstTeamSpaceId([personalRoot])).toBeNull();
  });
});

// ── OP-12b: one name, one heading ───────────────────────────────────────────

describe("the Personal heading is the private space (OP-12b)", () => {
  const stored = node("root", { name: "My tasks", personal: true, children: [personalArea] });

  it("never draws the root as its own row", () => {
    const { root, rows } = personalHeading(personalRoots({ personal: [stored] }, true));
    expect(root?.id).toBe("root");
    expect(rows.map((r) => r.id)).toEqual(["area"]);
    expect(rows.some((r) => r.id === "root")).toBe(false);
  });

  it("shows the root as Personal whatever the stored name is", () => {
    const [shown] = personalRoots({ personal: [stored] }, true);
    expect(shown.name).toBe("Personal");
    expect(displayNodeName(stored)).toBe("Personal");
  });

  it("keeps a second root as a row, so nothing is hidden", () => {
    const second = node("second", { personal: true });
    expect(personalHeading([stored, second]).rows.map((r) => r.id)).toEqual(["area", "second"]);
  });
});

describe("displayNodeName", () => {
  it("gives Personal for a personal root and the stored name for any other node", () => {
    expect(displayNodeName({ name: "My tasks", personal: true })).toBe("Personal");
    expect(displayNodeName({ name: "My tasks", personal: true, parent_project_id: "r" })).toBe("My tasks");
    expect(displayNodeName({ name: "My tasks" })).toBe("My tasks");
    expect(displayNodeName({ name: "Ops", personal: false })).toBe("Ops");
  });

  it("names a hit by its project id", () => {
    expect(displayProjectName("root", "My tasks", "root")).toBe("Personal");
    expect(displayProjectName("p1", "My tasks", "root")).toBe("My tasks");
  });
});

describe("the empty Personal line", () => {
  it("shows with no children and hides with one", () => {
    expect(personalEmptyLine([node("root", { personal: true, children: [] })])).toMatch(/^No personal /);
    expect(personalEmptyLine([personalRoot])).toBeNull();
    expect(personalEmptyLine([])).toBeNull();
  });

  it("names the folder in the private space's own empty state", () => {
    const text = emptyCopy(
      {
        id: "root", name: "Personal", level: "space", tasks: 11, overdue: 0, projects: 0,
        by_category: { done: 7 }, children: [],
      },
      { personal: true },
    );
    expect(text).toBe("11 tasks (4 open), in no folder. Add a folder or a project to group them.");
  });
});

describe("counts say what they count", () => {
  it("the sidebar count and the Overview open count agree for one node", () => {
    const summary = { tasks: 11, by_category: { done: 6, cancelled: 1 } };
    const row = node("root", { personal: true, tasks: 11, done: 6, cancelled: 1 });
    expect(sidebarOpen(row)).toBe(4);
    expect(overviewOpen(summary)).toBe(4);
  });
});

describe("a server row from the personal root reads Personal (OP-12b fix round 1)", () => {
  it("maps by project id, and by stored name when the row has no id", () => {
    rememberPersonalRoot([node("root", { name: "My tasks", personal: true })]);
    expect(displayRowProject({ project_id: "root", project_name: "My tasks" })).toBe("Personal");
    // No id, no match: a team project named "My tasks" keeps its name.
    expect(displayRowProject({ project_name: "My tasks" })).toBe("My tasks");
    expect(displayRowProject({ project_id: "pb", project_name: "My tasks" })).toBe("My tasks");
    expect(displayRowProject({ project_id: "pb", project_name: "Sales" })).toBe("Sales");
  });

  it("every surface that prints a server project_name goes through the seam", () => {
    for (const file of ["NeedsAttentionList", "TriageRail", "ReportsView", "AnalyticsPanels"]) {
      const source = readFileSync(`src/app/projects/components/${file}.tsx`, "utf-8");
      expect(source, file).toContain("displayRowProject(");
      expect(source, file).not.toMatch(/\{(row|hit|f|r)\.project_name\}/);
    }
  });
});

describe("the Folders and projects tile", () => {
  it("counts every live folder and project under the root, at any depth", () => {
    const deep = node("deep", { kind: "folder", children: [node("p1"), node("p2", { archived_at: "2026-10-30" })] });
    const filed = node("filed", { archived_at: "2026-10-30", children: [node("inside")] });
    expect(personalPlaceCount(node("root", { children: [deep, filed, node("p3")] }))).toBe(3);
    expect(personalPlaceCount(node("root"))).toBe(0);
  });
});
