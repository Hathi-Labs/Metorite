import { describe, expect, it } from "vitest";

import { groupByPath, splitArchived } from "./archivedRoots";
import type { ProjectRow } from "./api";

const row = (id: string, children: ProjectRow[] = [], archived = false, kind?: string) =>
  ({
    id,
    name: id,
    kind,
    archived_at: archived ? "2026-10-01T00:00:00Z" : null,
    children,
  }) as unknown as ProjectRow;

describe("splitArchived", () => {
  it("leaves a tree with nothing filed untouched", () => {
    const roots = [row("s", [row("p")])];
    const out = splitArchived(roots);
    expect(out.active[0]).toBe(roots[0]);
    expect(out.archived).toEqual([]);
    expect(out.count).toBe(0);
  });

  it("lifts a filed branch out and counts every row in it", () => {
    const out = splitArchived([row("s", [row("p", [row("sub", [], true)], true), row("q")])]);
    expect(out.active[0].children?.map((c) => c.id)).toEqual(["q"]);
    expect(out.archived.map((a) => a.node.id)).toEqual(["p"]);
    expect(out.count).toBe(2);
  });

  it("keeps the path and the generation of a project filed on its own", () => {
    const out = splitArchived([row("s", [row("p", [row("sub", [], true)])])]);
    expect(out.archived).toHaveLength(1);
    expect(out.archived[0].path).toEqual(["s", "p"]);
    expect(out.archived[0].parentGen).toBe(2);
  });

  it("lists a filed space with an empty path", () => {
    const out = splitArchived([row("s", [], true), row("t")]);
    expect(out.archived[0].path).toEqual([]);
    expect(out.archived[0].parentGen).toBe(0);
    expect(out.active.map((r) => r.id)).toEqual(["t"]);
  });

  // ARC-BUG-1 (owner, 2026-11-08): a project filed inside a live folder drew
  // twice, in the live tree and in the Archived section. The space kept its
  // OLD folder object, because the space's own child count did not change.
  it("takes a filed grandchild out of the live tree", () => {
    const roots = [
      row("space", [row("ops"), row("folder", [row("filed", [], true), row("live")], false, "folder")]),
    ];
    const out = splitArchived(roots);
    expect(out.archived.map((a) => a.node.id)).toEqual(["filed"]);
    expect(out.active[0].children?.[1].children?.map((c) => c.id)).toEqual(["live"]);
    expect(liveIds(out.active)).not.toContain("filed");
  });

  it("never leaves a filed row anywhere in the live tree, at any depth", () => {
    const roots = [
      row("s", [row("a", [row("b", [row("c", [row("deep", [], true)])])]), row("x", [row("y", [], true)])]),
    ];
    const out = splitArchived(roots);
    expect(liveIds(out.active)).toEqual(["s", "a", "b", "c", "x"]);
    expect(out.archived.map((a) => a.node.id)).toEqual(["deep", "y"]);
  });
});

describe("groupByPath", () => {
  it("prints one path for filed siblings, and a new one where the parent changes", () => {
    const out = splitArchived([
      row("s", [row("f", [row("one", [], true), row("live"), row("two", [], true)]), row("three", [], true)]),
    ]);
    const groups = groupByPath(out.archived);
    expect(groups.map((g) => [g.path, g.entries.map((e) => e.node.id)])).toEqual([
      [["s", "f"], ["one", "two"]],
      [["s"], ["three"]],
    ]);
  });
});

function liveIds(nodes: readonly ProjectRow[]): string[] {
  return nodes.flatMap((n) => [n.id, ...liveIds(n.children ?? [])]);
}
