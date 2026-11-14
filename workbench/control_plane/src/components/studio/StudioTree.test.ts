/**
 * STU-D18-2b fence: the Tree block.
 *
 * The vitest env has no DOM and takes `*.test.ts` only. So the view draws from props with
 * `renderToStaticMarkup`, and the move runs as the plain store it is (`treeMove.ts`).
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { StudioTreeView, TREE_EMPTY_LINE, TREE_TRUNCATED_LINE, parentOptions } from "@/components/studio/StudioTree";
import { withMeta, type ActionResult, type Row, type StudioDataDoor } from "@/lib/studio/dataDoor";
import { MAX_TREE_NODES, type TreeNodeIn } from "@/lib/studio/treeModel";
import { createTreeMover, TOP_OVER } from "@/lib/studio/treeMove";
import type { ViewTree } from "@/lib/studio/viewBlocks";

const model = (over: Partial<ViewTree> = {}): ViewTree => ({
  kind: "tree", title: "Parts", object: "part", field: "parent", labelField: "name",
  metaFields: [{ key: "code", type: "text", label: "Code" }],
  source: { action: "object:part.tree", args: { limit: MAX_TREE_NODES } },
  canMove: true, update: "object:part.update", ...over,
});
const door: StudioDataDoor = { scope: "t:live", read: async () => [], run: vi.fn(async () => ({ ok: true })) };

const row = (id: string, parent: string | null, name = `Part ${id}`): Row =>
  ({ id, name, code: `C-${id}`, __parent: parent, __level: parent ? 2 : 1 });
const small: Row[] = [row("a", null), row("b", "a"), row("c", "b"), row("d", "a"), row("e", null)];

const html = (rows: Row[], m: ViewTree = model(), extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(createElement(StudioTreeView, { model: m, rows, door, ...extra }));
const count = (text: string, needle: RegExp) => (text.match(needle) ?? []).length;

describe("the markup", () => {
  it("is a tree with treeitems, levels and aria-expanded", () => {
    const out = html(small);
    expect(out).toContain('role="tree"');
    expect(count(out, /role="treeitem"/g)).toBe(4); // a and e, and the children b and d of the open a
  });

  it("opens the first level only: a is open, b stays closed", () => {
    const out = html(small);
    // a, b, d, e are shown. c is under the closed b.
    expect(out).toContain("Part a");
    expect(out).toContain("Part b");
    expect(out).not.toContain("Part c");
    expect(out).toMatch(/aria-level="1"[^>]*aria-expanded="true"/);
    expect(out).toMatch(/aria-level="2"[^>]*aria-expanded="false"/);
    expect(out).toContain('aria-label="Expand Part b"');
    expect(out).toContain('aria-label="Collapse Part a"');
  });

  it("draws the label and up to two muted meta values", () => {
    const out = html(small);
    expect(out).toContain("C-a");
    expect(out).toContain("text-muted-foreground");
  });

  it("draws the first level only for a tree of 2,000 nodes", () => {
    const rows: Row[] = [row("r", null)];
    for (let i = 0; i < MAX_TREE_NODES - 1; i++) rows.push(row(`n${i}`, i === 0 ? "r" : `n${Math.floor((i - 1) / 3)}`));
    const out = html(rows);
    // The root is open, so its children show. No grandchild shows.
    const items = count(out, /role="treeitem"/g);
    const levels = [...out.matchAll(/aria-level="(\d+)"/g)].map((m) => Number(m[1]));
    expect(Math.max(...levels)).toBe(2);
    expect(items).toBeLessThan(rows.length);
    expect(items).toBe(levels.length);
    expect(items).toBe(1 + rows.filter((r) => r.__parent === "r").length);
  });

  it("draws no row for a collapsed child", () => {
    const flat: Row[] = [row("r", null), ...Array.from({ length: 50 }, (_, i) => row(`k${i}`, "r"))];
    const deep: Row[] = [...flat, ...Array.from({ length: 50 }, (_, i) => row(`g${i}`, "k0"))];
    expect(count(html(deep), /role="treeitem"/g)).toBe(51);
  });

  it("says the empty state", () => {
    expect(html([])).toContain(TREE_EMPTY_LINE);
    expect(TREE_EMPTY_LINE).toBe("No records yet.");
  });

  it("says the truncated line only when the read says so", () => {
    expect(html(small)).not.toContain("Narrow the tree");
    const cut = withMeta([...small], { truncated: true });
    expect(html(cut)).toContain(TREE_TRUNCATED_LINE);
    expect(TREE_TRUNCATED_LINE).toBe("Showing the first 2,000 records. Narrow the tree to see the rest.");
  });

  it("has a status line, and an alert line after a refusal", async () => {
    expect(html(small)).toContain('role="status"');
    expect(html(small)).not.toContain('role="alert"');
    const mover = createTreeMover(door, "u", "parent");
    const nodes: TreeNodeIn[] = [{ id: "a", parentId: null }, { id: "b", parentId: "a" }];
    await mover.move(nodes, "a", "b", (id) => id);
    const out = html(small, model(), { mover });
    expect(out).toContain('role="alert"');
    expect(out).toContain("A node cannot move under one of its own children.");
  });
});

describe("who may move", () => {
  it("draws a drag, a Top level target and the Move under control when canMove", () => {
    const out = html(small);
    expect(out).toContain('draggable="true"');
    expect(out).toContain("Top level");
    expect(out).toContain('aria-label="Move Part a under"');
    expect(out).toContain("Move to…");
  });

  it("draws no drag, no Top level and no Move under for a viewer", () => {
    const out = html(small, model({ canMove: false, update: null }));
    expect(out).not.toContain('draggable="true"');
    expect(out).not.toContain("Top level");
    expect(out).not.toContain("Move Part a under");
    expect(out).not.toContain("Move to");
    // The tree still opens and reads.
    expect(out).toContain('aria-label="Collapse Part a"');
  });

  it("locks the drag of a node with a call in flight", () => {
    let settle: (r: ActionResult) => void = () => {};
    const slow: StudioDataDoor = { scope: "t:live", read: async () => [], run: () => new Promise<ActionResult>((r) => { settle = r; }) };
    const mover = createTreeMover(slow, "u", "parent");
    void mover.move([{ id: "a", parentId: null }, { id: "e", parentId: null }], "e", "a", (id) => id);
    const out = html(small, model(), { mover });
    expect(out).toContain('aria-busy="true"');
    expect(out).toMatch(/data-node-id="e"[^>]*draggable="false"|draggable="false"[^>]*data-node-id="e"/);
    settle({ ok: true });
  });

  it("draws the drop ring on the row under a drag", () => {
    const mover = createTreeMover(door, "u", "parent");
    mover.dragStart("e");
    mover.dragOver("d");
    expect(html(small, model(), { mover })).toContain("ring-2 ring-primary/40");
    mover.dragOver(TOP_OVER);
    const out = html(small, model(), { mover });
    expect(out).toContain("ring-2 ring-primary/40");
  });
});

describe("the Move under list", () => {
  const nodes: TreeNodeIn[] = [
    { id: "a", parentId: null, label: "A" }, { id: "b", parentId: "a", label: "B" },
    { id: "c", parentId: "b", label: "C" }, { id: "e", parentId: null, label: "E" },
  ];
  it("lists the top level and each node, and disables the moves that the model refuses", () => {
    const opts = parentOptions(nodes, "a");
    expect(opts.map((o) => o.label)).toEqual(["Top level", "A", "B", "C", "E"]);
    const by = Object.fromEntries(opts.map((o) => [o.label, o]));
    expect(by["Top level"].disabled).toBe(true);
    expect(by["Top level"].hint).toBe("The node is already there.");
    expect(by.A.hint).toBe("A node cannot move under itself.");
    expect(by.C.hint).toBe("A node cannot move under one of its own children.");
    expect(by.E.disabled).toBeUndefined();
    expect(by.C.depth).toBe(2);
  });
});

describe("the source", () => {
  const src = readFileSync(new URL("./StudioTree.tsx", import.meta.url), "utf8");
  it("holds no raw colour, no lucide import, no raw button and no inner html", () => {
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(src).not.toContain("lucide-react");
    expect(src).not.toContain("dangerouslySetInnerHTML");
    expect(src).not.toMatch(/<button\b/);
    expect(src).not.toMatch(/<select\b/);
    expect(src).not.toContain("NEXT_PUBLIC_STUDIO");
    expect(src).not.toContain("onKeyDown");
  });
  it("does not import the grant tree", () => {
    expect(src).not.toContain("AccessTree");
  });
});
