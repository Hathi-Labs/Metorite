/**
 * WS-64 — a filed (archived) project is in no picker and no overview table.
 *
 * The tree read returns archived rows on purpose, because the rail splits them
 * out (`archivedRoots.ts`). Every list a member picks from reads the active
 * rows only. The server refuses a placement under a filed project, so a picker
 * that offered one would teach by a 422.
 */
import { describe, expect, it } from "vitest";

import type { SummaryChild } from "./api";
import { dashboardRows } from "./dashboardRows";
import { destinations } from "./destinations";
import { pickerNodes } from "./pickerTree";
import { scopeOptions } from "./reportBuilder";
import { type ProjectNode, flattenActive } from "./tree";

const FILED = "2026-10-01T00:00:00Z";

const TREE: ProjectNode[] = [
  {
    id: "live",
    name: "Live space",
    children: [
      { id: "live-a", name: "Live A" },
      { id: "filed-b", name: "Filed B", archived_at: FILED, children: [
        { id: "under-b", name: "Under B", archived_at: FILED },
      ] },
    ],
  },
  { id: "filed-space", name: "Filed space", archived_at: FILED, children: [
    { id: "under-space", name: "Under space", archived_at: FILED },
  ] },
];

describe("destinations", () => {
  it("leaves out a filed node and everything under it", () => {
    expect(destinations(TREE).map((r) => r.node.id)).toEqual(["live", "live-a"]);
  });

  it("can include them for a caller that must still name one", () => {
    expect(destinations(TREE, 0, true).map((r) => r.node.id)).toContain("filed-b");
  });
});

describe("pickerNodes", () => {
  it("offers only active rows", () => {
    expect(pickerNodes(TREE).map((n) => n.id)).toEqual(["live", "live-a"]);
  });

  it("does not draw a chevron for a node whose only children are filed", () => {
    const tree: ProjectNode[] = [
      { id: "s", name: "S", children: [{ id: "f", name: "F", archived_at: FILED }] },
    ];
    expect(pickerNodes(tree)[0].hasChildren).toBe(false);
  });

  it("still finds a filed node when asked, for a label that already holds it", () => {
    expect(pickerNodes(TREE, undefined, true).map((n) => n.id)).toContain("filed-b");
  });
});

describe("the report scope chip", () => {
  it("lists the whole organization and the active nodes only", () => {
    expect(scopeOptions(TREE as never).map((o) => o.value)).toEqual(["", "live", "live-a"]);
  });
});

describe("flattenActive", () => {
  it("skips a filed branch whole", () => {
    expect(flattenActive(TREE).map((e) => e.node.id)).toEqual(["live", "live-a"]);
  });
});

describe("the overview rows", () => {
  const child = (id: string, archived: boolean): SummaryChild => ({
    id,
    name: id,
    kind: "project",
    archived,
    tasks: 1,
    overdue: 0,
    by_category: { todo: 1 },
  });

  it("drops a filed child that an older gateway still sends", () => {
    const rows = dashboardRows({
      id: "P",
      name: "P",
      level: "project",
      tasks: 2,
      overdue: 0,
      by_category: {},
      projects: 0,
      children: [child("a", false), child("b", true)],
    });
    expect(rows.map((r) => (r.kind === "child" ? r.child.id : r.kind))).toEqual(["a"]);
  });
});
