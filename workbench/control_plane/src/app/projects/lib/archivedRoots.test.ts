import { describe, expect, it } from "vitest";

import { splitArchived } from "./archivedRoots";
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
});
