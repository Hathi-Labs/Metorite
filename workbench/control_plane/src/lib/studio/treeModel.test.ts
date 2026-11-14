/**
 * STU-D18-1 fence: the pure model of the Tree block.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  MAX_TREE_DEPTH, MAX_TREE_NODES, applyMove, buildTree, canMove, hierarchyField, initialExpanded, moveRefusal, movePatch,
  treeRows, type TreeNodeIn,
} from "./treeModel";

const n = (id: string, parentId: string | null = null): TreeNodeIn => ({ id, parentId, label: id });

/** 13 roots x 12 children x 12 grandchildren is 2,041 nodes. Cut to exactly 2,000. */
function fixture(): TreeNodeIn[] {
  const out: TreeNodeIn[] = [];
  for (let a = 0; a < 13; a++) out.push(n(`r${a}`));
  for (let a = 0; a < 13; a++) for (let b = 0; b < 12; b++) out.push(n(`r${a}c${b}`, `r${a}`));
  for (let a = 0; a < 13; a++) for (let b = 0; b < 12; b++) for (let c = 0; c < 12; c++) out.push(n(`r${a}c${b}g${c}`, `r${a}c${b}`));
  return out.slice(0, 2000);
}

function chain(len: number): TreeNodeIn[] {
  return Array.from({ length: len }, (_, i) => n(`n${i}`, i === 0 ? null : `n${i - 1}`));
}

function deepFreeze<T>(v: T): T {
  if (v && typeof v === "object" && !Object.isFrozen(v)) {
    Object.freeze(v);
    for (const k of Object.keys(v)) deepFreeze((v as Record<string, unknown>)[k]);
  }
  return v;
}

describe("treeRows on 2,000 nodes", () => {
  const nodes = fixture();
  it("has exactly 2,000 nodes", () => expect(nodes).toHaveLength(2000));
  it("shows roots and their children when the first level is open", () => {
    const rows = treeRows(nodes, initialExpanded(nodes));
    expect(rows).toHaveLength(13 + 13 * 12);
    expect(rows.filter((r) => r.depth === 0)).toHaveLength(13);
    expect(rows.filter((r) => r.depth === 1)).toHaveLength(156);
    expect(rows.every((r) => r.depth <= 1)).toBe(true);
  });
  it("shows only roots when nothing is open", () => {
    const rows = treeRows(nodes, new Set());
    expect(rows).toHaveLength(13);
    expect(rows.every((r) => r.hasChildren && !r.expanded)).toBe(true);
  });
  it("opens two levels", () => {
    expect(treeRows(nodes, initialExpanded(nodes, 2))).toHaveLength(2000);
    expect(initialExpanded(nodes, 0).size).toBe(0);
  });
  it("lists depth first", () => {
    const rows = treeRows(nodes, initialExpanded(nodes, 2));
    expect(rows.slice(0, 4).map((r) => r.id)).toEqual(["r0", "r0c0", "r0c0g0", "r0c0g1"]);
    expect(rows[13].id).toBe("r0c0g11");
    expect(rows[14].id).toBe("r0c1");
  });
  it("expands and collapses one node", () => {
    const open = initialExpanded(nodes);
    const more = new Set(open).add("r0c0");
    expect(treeRows(nodes, more)).toHaveLength(169 + 12);
    const less = new Set(open);
    less.delete("r0");
    expect(treeRows(nodes, less)).toHaveLength(169 - 12);
  });
  it("marks hasChildren and expanded", () => {
    const rows = treeRows(nodes, initialExpanded(nodes));
    expect(rows[0]).toMatchObject({ id: "r0", hasChildren: true, expanded: true, depth: 0, parentId: null });
    expect(rows[1]).toMatchObject({ id: "r0c0", hasChildren: true, expanded: false, depth: 1, parentId: "r0" });
    const leaf = treeRows(nodes, initialExpanded(nodes, 2)).find((r) => r.id === "r0c0g0");
    expect(leaf).toMatchObject({ hasChildren: false, expanded: false, depth: 2 });
  });
  it("sorts by label when asked", () => {
    const rows = treeRows([n("b"), n("a"), n("c", "a")], new Set(["a"]), { sort: "label" });
    expect(rows.map((r) => r.id)).toEqual(["a", "c", "b"]);
    expect(treeRows([n("b"), n("a")], new Set()).map((r) => r.id)).toEqual(["b", "a"]);
  });
});

describe("a chain of 2,000 levels", () => {
  it("does not overflow the stack", () => {
    const nodes = chain(2000);
    const all = new Set(nodes.map((x) => x.id));
    const rows = treeRows(nodes, all);
    expect(rows).toHaveLength(2000);
    expect(rows[1999].depth).toBe(1999);
    expect(initialExpanded(nodes, 5000).size).toBe(1999);
    expect(canMove(nodes, "n0", "n1999")).toEqual({ ok: false, reason: "descendant" });
  });
});

describe("buildTree", () => {
  const label = (r: Record<string, unknown>) => String(r.name ?? "");
  it("reads a string id, an {id} object, null and an empty string", () => {
    const rows = [
      { id: "a", name: "A", parent: null },
      { id: "b", name: "B", parent: { id: "a" } },
      { id: "c", name: "C", parent: "a" },
      { id: "d", name: "D", parent: "" },
      { id: "e", name: "E" },
    ];
    const t = buildTree(rows, "parent", label);
    expect(t.map((x) => x.parentId)).toEqual([null, "a", "a", null, null]);
    expect(t.map((x) => x.label)).toEqual(["A", "B", "C", "D", "E"]);
    expect(t.some((x) => x.orphan || x.cycle)).toBe(false);
    expect(t[0].data).toBe(rows[0]);
  });
  it("keeps an orphan as a flagged root", () => {
    const t = buildTree([{ id: "a", parent: "gone" }], "parent", label);
    expect(t).toHaveLength(1);
    expect(t[0]).toMatchObject({ id: "a", orphan: true });
    expect(treeRows(t, new Set())[0]).toMatchObject({ id: "a", depth: 0, orphan: true });
  });
  it("keeps the first of a duplicate id", () => {
    const t = buildTree([{ id: "a", name: "one" }, { id: "a", name: "two" }], "parent", label);
    expect(t).toHaveLength(1);
    expect(t[0].label).toBe("one");
  });
  it("uses the id when the label is empty, and survives a label that throws", () => {
    const t = buildTree([{ id: "a" }, { id: "b" }], "parent", (r) => { if (r.id === "b") throw new Error("x"); return ""; });
    expect(t.map((x) => x.label)).toEqual(["a", "b"]);
  });
  it("makes a self-parent a root with cycle", () => {
    const t = buildTree([{ id: "a", parent: "a" }], "parent", label);
    expect(t[0]).toMatchObject({ parentId: null, cycle: true });
  });
  it("breaks a cycle at the first node in the input", () => {
    const rows = [{ id: "x", parent: null }, { id: "a", parent: "b" }, { id: "b", parent: "a" }, { id: "c", parent: "b" }];
    const t = buildTree(rows, "parent", label);
    expect(t.find((x) => x.id === "a")).toMatchObject({ parentId: null, cycle: true });
    expect(t.find((x) => x.id === "b")).toMatchObject({ parentId: "a" });
    expect(t.filter((x) => x.cycle)).toHaveLength(1);
    expect(treeRows(t, new Set(["a", "b"])).map((r) => r.id)).toEqual(["x", "a", "b", "c"]);
  });
  it("does not loop on a long cycle", () => {
    const rows = Array.from({ length: 2000 }, (_, i) => ({ id: `n${i}`, parent: `n${(i + 1) % 2000}` }));
    const t = buildTree(rows, "parent", label);
    expect(t.filter((x) => x.cycle)).toHaveLength(1);
    expect(treeRows(t, new Set(t.map((x) => x.id)))).toHaveLength(2000);
  });
  it("answers an empty list for junk", () => {
    expect(buildTree(null as unknown as [], "p", label)).toEqual([]);
    expect(buildTree([null, 3, { id: 4 }, { id: "" }] as unknown as [], "p", label)).toEqual([]);
  });
});

describe("canMove", () => {
  const t = [n("a"), n("b", "a"), n("c", "b"), n("d"), n("e", "d")];
  it("allows a move to another parent and to the top level", () => {
    expect(canMove(t, "c", "d")).toEqual({ ok: true });
    expect(canMove(t, "c", null)).toEqual({ ok: true });
    expect(canMove(t, "a", "d")).toEqual({ ok: true });
  });
  it("refuses a drop on itself", () => expect(canMove(t, "b", "b")).toEqual({ ok: false, reason: "self" }));
  it("refuses a drop on a descendant", () => {
    expect(canMove(t, "a", "b")).toEqual({ ok: false, reason: "descendant" });
    expect(canMove(t, "a", "c")).toEqual({ ok: false, reason: "descendant" });
  });
  it("refuses the same parent, also for the top level", () => {
    expect(canMove(t, "b", "a")).toEqual({ ok: false, reason: "same_parent" });
    expect(canMove(t, "a", null)).toEqual({ ok: false, reason: "same_parent" });
  });
  it("refuses an unknown node and an unknown parent", () => {
    expect(canMove(t, "zz", "a")).toEqual({ ok: false, reason: "unknown_node" });
    expect(canMove(t, "a", "zz")).toEqual({ ok: false, reason: "unknown_parent" });
  });
  it("allows an orphan to move to the top level", () => {
    expect(canMove([{ id: "o", parentId: "gone", orphan: true }], "o", null)).toEqual({ ok: true });
  });
  it("counts the height of the moved subtree against the cap", () => {
    // A chain of 20 levels, and a separate root `s` with one child `s1` (2 levels).
    const nodes = [...chain(MAX_TREE_DEPTH), n("s"), n("s1", "s")];
    const last = `n${MAX_TREE_DEPTH - 1}`;
    // A leaf under the 20th level would be level 21.
    expect(canMove(nodes, "s1", last)).toEqual({ ok: false, reason: "too_deep" });
    // The subtree `s` (2 levels) under level 18 reaches level 20.
    expect(canMove(nodes, "s", `n${MAX_TREE_DEPTH - 3}`)).toEqual({ ok: true });
    // Under level 19 it reaches level 21.
    expect(canMove(nodes, "s", `n${MAX_TREE_DEPTH - 2}`)).toEqual({ ok: false, reason: "too_deep" });
    // A leaf under level 19 reaches level 20.
    expect(canMove([...chain(MAX_TREE_DEPTH - 1), n("leaf")], "leaf", `n${MAX_TREE_DEPTH - 2}`)).toEqual({ ok: true });
  });
  it("never throws on junk", () => {
    expect(canMove(null as unknown as [], "a", null)).toEqual({ ok: false, reason: "unknown_node" });
  });
  it("gives a plain sentence for each reason", () => {
    for (const r of ["self", "descendant", "too_deep", "unknown_node", "unknown_parent", "same_parent"] as const) {
      expect(moveRefusal(r)).toMatch(/^[A-Z][^;]+\.$/);
    }
    expect(moveRefusal("self")).toBe("A node cannot move under itself.");
    expect(moveRefusal("descendant")).toBe("A node cannot move under one of its own children.");
    expect(moveRefusal("too_deep")).toBe("The tree cannot go deeper than 20 levels.");
  });
});

describe("movePatch and applyMove", () => {
  it("sends the {id} relation value, and null for the top level", () => {
    expect(movePatch("parent", "c", "d")).toEqual({ id: "c", data: { parent: { id: "d" } } });
    expect(movePatch("parent", "c", null)).toEqual({ id: "c", data: { parent: null } });
  });
  it("changes only the parent of the node", () => {
    const t = [n("a"), n("b", "a"), n("d")];
    const next = applyMove(t, "b", "d");
    expect(next).not.toBe(t);
    expect(next.map((x) => x.parentId)).toEqual([null, "d", null]);
    expect(next[0]).toBe(t[0]);
    expect(t[1].parentId).toBe("a");
    expect(applyMove(t, "b", null)[1].parentId).toBeNull();
  });
  it("clears the orphan flag of a moved node", () => {
    const next = applyMove([n("a"), { id: "o", parentId: "gone", orphan: true }], "o", "a");
    expect(next[1]).toEqual({ id: "o", parentId: "a" });
  });
  it("returns the same array for a refused move", () => {
    const t = [n("a"), n("b", "a")];
    expect(applyMove(t, "a", "b")).toBe(t);
    expect(applyMove(t, "a", "a")).toBe(t);
    expect(applyMove(t, "x", "a")).toBe(t);
    expect(applyMove(t, "b", "a")).toBe(t);
  });
});

describe("hierarchyField", () => {
  it("reads the first hierarchy rule", () => {
    const rules = [
      { name: "f", kind: "formula", target: "x" },
      { name: "h1", kind: "hierarchy", field: "parent" },
      { name: "h2", kind: "hierarchy", field: "other" },
    ];
    expect(hierarchyField({ rules })).toEqual({ rule: "h1", field: "parent" });
  });
  it("answers null for none and for junk", () => {
    expect(hierarchyField({ rules: [] })).toBeNull();
    expect(hierarchyField({})).toBeNull();
    expect(hierarchyField(null)).toBeNull();
    expect(hierarchyField("x")).toBeNull();
    expect(hierarchyField({ rules: "x" })).toBeNull();
    expect(hierarchyField({ rules: [null, 3, { kind: "hierarchy" }] })).toBeNull();
    expect(hierarchyField({ rules: [{ kind: "hierarchy", field: 4 }] })).toBeNull();
  });
});

describe("purity and input", () => {
  it("never changes a frozen input", () => {
    const rows = deepFreeze([{ id: "a", p: null }, { id: "b", p: { id: "a" } }, { id: "c", p: "b" }]);
    const nodes = deepFreeze(buildTree(rows, "p", (r) => String(r.id)));
    const open = new Set(["a"]);
    expect(() => {
      treeRows(nodes, open, { sort: "label" });
      initialExpanded(nodes, 3);
      canMove(nodes, "a", "c");
      canMove(nodes, "c", null);
      applyMove(nodes, "c", "a");
    }).not.toThrow();
    expect([...open]).toEqual(["a"]);
  });
  it("has no React, clock, random or network", () => {
    const src = readFileSync(resolve(__dirname, "treeModel.ts"), "utf-8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(src).not.toMatch(/\bfrom\s+["']react|\bDate\b|Math\.random|\bfetch\s*\(|\bimport\b[^;]*\bdoor/i);
  });
});

describe("the constants equal the Python twins", () => {
  const root = resolve(__dirname, "../../../../..");
  const py = (rel: string) => readFileSync(resolve(root, rel), "utf-8");
  it("MAX_TREE_NODES", () => {
    const m = /^MAX_TREE_NODES\s*=\s*([\d_]+)/m.exec(py("apps/services/gateway/gateway/studio/objects_store.py"));
    expect(m).not.toBeNull();
    expect(Number(m![1].replace(/_/g, ""))).toBe(MAX_TREE_NODES);
  });
  it("MAX_TREE_DEPTH", () => {
    const m = /^MAX_TREE_DEPTH\s*=\s*(\d+)/m.exec(py("packages/acb_common/acb_common/studio_rules.py"));
    expect(m).not.toBeNull();
    expect(Number(m![1])).toBe(MAX_TREE_DEPTH);
  });
});
