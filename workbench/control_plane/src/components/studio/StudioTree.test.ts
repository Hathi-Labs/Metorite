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

import { StudioTreeView, TREE_EMPTY_LINE, TREE_TRUNCATED_LINE, overTarget, parentOptions } from "@/components/studio/StudioTree";
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
    // "d" is not a root, so the top level takes it.
    const other = createTreeMover(door, "u", "parent");
    other.dragStart("d");
    other.dragOver(TOP_OVER);
    expect(html(small, model(), { mover: other })).toContain("ring-2 ring-primary/40");
  });

  it("draws no ring on a target that the drop would refuse", () => {
    const mover = createTreeMover(door, "u", "parent");
    mover.dragStart("a");
    mover.dragOver("a"); // itself
    expect(html(small, model(), { mover })).not.toContain("ring-2 ring-primary/40");
    mover.dragOver("d"); // its own child
    expect(html(small, model(), { mover })).not.toContain("ring-2 ring-primary/40");
    mover.dragOver(TOP_OVER); // a is a root already
    expect(html(small, model(), { mover })).not.toContain("ring-2 ring-primary/40");
  });
});

describe("overTarget: the drag over a target", () => {
  const nodes: TreeNodeIn[] = [
    { id: "a", parentId: null }, { id: "b", parentId: "a" }, { id: "e", parentId: null },
  ];
  it("allows a valid target: the ring state is set and the view calls preventDefault", () => {
    const mover = createTreeMover(door, "u", "parent");
    mover.dragStart("b");
    expect(overTarget(mover, nodes, "e")).toBe(true);
    expect(mover.getState().over).toBe("e");
    expect(overTarget(mover, nodes, null)).toBe(true);
    expect(mover.getState().over).toBe(TOP_OVER);
  });
  it("refuses an invalid target: no preventDefault and the ring clears", () => {
    const mover = createTreeMover(door, "u", "parent");
    mover.dragStart("a");
    expect(overTarget(mover, nodes, "e")).toBe(true);
    expect(overTarget(mover, nodes, "b")).toBe(false); // its own child
    expect(mover.getState().over).toBeNull();
    expect(overTarget(mover, nodes, "a")).toBe(false); // itself
    expect(overTarget(mover, nodes, null)).toBe(false); // a is a root already
  });
  it("does nothing when no drag runs", () => {
    const mover = createTreeMover(door, "u", "parent");
    expect(overTarget(mover, nodes, "e")).toBe(false);
  });
});

describe("the layout rules", () => {
  const src = readFileSync(new URL("./StudioTree.tsx", import.meta.url), "utf8");
  it("keeps one fixed line for the alert and the status, so the tree does not jump", () => {
    expect(src).toMatch(/const NOTICE_SLOT = "min-h-6"/);
    expect(src).toMatch(/<div className=\{NOTICE_SLOT\}>[\s\S]*role="alert"[\s\S]*role="status"[\s\S]*<\/div>/);
    expect(src).not.toContain("empty:hidden");
    expect(html(small)).toMatch(/<div class="min-h-6"><p role="status"[^>]*><\/p><\/div>/);
  });
  it("gives the label the room, and keeps the swapped control narrow", () => {
    expect(html(small)).toMatch(/<span class="min-w-0 flex-1 truncate text-sm text-foreground">Part a<\/span>/);
    expect(src).toContain('const MOVE_WIDTH = "w-28"');
    expect(src).toContain("widthClass={MOVE_WIDTH}");
  });
  it("gives a leaf the width of the chevron button, so the grips line up", () => {
    expect(src).toContain('const CHEVRON_SPACER = "w-6 glass:w-7 glass:pointer-coarse:w-10"');
    expect(html(small)).toContain("inline-block shrink-0 w-6 glass:w-7 glass:pointer-coarse:w-10");
  });
  it("hides the grip on a touch screen: Move to is the path there", () => {
    expect(html(small)).toContain("lucide-grip-vertical h-3.5 w-3.5 shrink-0 cursor-grab text-muted-foreground pointer-coarse:hidden");
    expect(src).toContain("pointer-coarse:hidden");
  });
  it("hangs the Move list from the right edge, and returns focus to the row when it closes", () => {
    expect(src).toContain('align="end"');
    expect(src).toMatch(/slots\.current\.get\(moving\)\?\.querySelector\("button"\)\?\.focus\(\)/);
    expect(src).toMatch(/slots\.current\.get\(refocus\.current\)\?\.querySelector\("button"\)/);
    expect(src).toMatch(/\[moving\]\);/);
  });
  it("lets the swapped control take its own full-width line below sm, and the label keep the first line", () => {
    expect(src).toContain('const MOVING_ROW = "flex-wrap sm:flex-nowrap"');
    expect(src).toContain('const MOVING_SLOT = "shrink-0 basis-full sm:basis-auto"');
    expect(src).toMatch(/moving === row\.id \? MOVING_ROW : ""/);
    expect(src).toMatch(/className=\{moving === row\.id \? MOVING_SLOT : "shrink-0"\}/);
    expect(html(small)).toMatch(/<span class="min-w-0 flex-1 truncate text-sm text-foreground">Part a<\/span>/);
    // A row that is not swapped keeps the plain slot and no wrap.
    expect(html(small)).not.toContain("basis-full");
    expect(html(small)).not.toContain("flex-wrap");
  });
});

describe("the pick path: the menu does not reopen at the new place", () => {
  const src = readFileSync(new URL("./StudioTree.tsx", import.meta.url), "utf8");
  it("clears the moving row in the pick handler, before the move is sent", () => {
    expect(src).toMatch(/onChange=\{\(v\) => \{ setMoving\(null\); send\(row\.id, v === TOP_OVER \? null : v\); \}\}/);
    expect(src).toContain("onClose={() => setMoving(null)}");
  });
  it("returns focus to the Move button of the row where it is now, also after it remounts", () => {
    expect(src).toContain("const refocus = useRef<string | null>(null)");
    expect(src).toMatch(/\[moving, list, st\.pending\]\);/);
    expect(src).toMatch(/if \(btn\) \{ btn\.focus\(\); refocus\.current = null; \}/);
  });
  it("a pick moves the row, and the view with no row in the swapped state draws the Move button again", async () => {
    const mover = createTreeMover(door, "u", "parent");
    const nodes: TreeNodeIn[] = [{ id: "a", parentId: null }, { id: "e", parentId: null }];
    await mover.move(nodes, "e", "a", (id) => id);
    const out = html(small, model(), { mover });
    expect(out).toContain('aria-label="Move Part e under"');
    expect(out).not.toContain("Move Part e under</");
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
    expect(by["Top level"].description).toBe("The node is already there.");
    expect(by.A.description).toBe("A node cannot move under itself.");
    expect(by.C.description).toBe("A node cannot move under one of its own children.");
    // The reason wraps. A one-line hint made the list wider than a phone.
    expect(opts.every((o) => o.hint === undefined)).toBe(true);
    expect(by.E.disabled).toBeUndefined();
    expect(by.C.depth).toBe(2);
  });
  it("caps the indent and sorts the nodes by label, numbers as numbers", () => {
    const deep: TreeNodeIn[] = [];
    for (let i = 0; i < 9; i++) deep.push({ id: `d${i}`, parentId: i === 0 ? null : `d${i - 1}`, label: `D${i}` });
    deep.push({ id: "x", parentId: null, label: "Top" });
    expect(Math.max(...parentOptions(deep, "x").map((o) => o.depth ?? 0))).toBe(5);
    const parts: TreeNodeIn[] = ["Part 10", "Part 2", "Part 1"].map((label, i) => ({ id: `p${i}`, parentId: null, label }));
    parts.push({ id: "z", parentId: null, label: "Zed" });
    expect(parentOptions(parts, "z").map((o) => o.label)).toEqual(["Top level", "Part 1", "Part 2", "Part 10", "Zed"]);
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
