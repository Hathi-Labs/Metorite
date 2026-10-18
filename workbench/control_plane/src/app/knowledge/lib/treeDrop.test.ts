/**
 * Where a dragged page lands (WS-61 KB-2e). Fence for `treeDrop.ts`: the
 * zones of a row, the body of each drop, and the refusal of a drop into the
 * page's own subtree.
 */
import { describe, expect, it } from "vitest";

import { applyMove, dropZoneAt, planDrop, subtreeIds, targetFor } from "./treeDrop";
import { buildTree } from "./tree";
import type { PageSummary } from "./types";

function page(id: string, parent: string | null, position: number, title = id): PageSummary {
  return {
    id,
    space_id: "s1",
    parent_id: parent,
    position,
    title,
    state: "published",
    author_email: "a@example.com",
    reviewers: [],
    review_at: null,
    source_ref: null,
    published_at: null,
    archived_at: null,
    history_from: null,
    archived_from_state: null,
    updated_at: null,
    created_at: null,
  };
}

// Root: A, B, C. Under A: A1, A2. Under A1: A1x.
const PAGES = [
  page("A", null, 0),
  page("B", null, 1),
  page("C", null, 2),
  page("A1", "A", 0),
  page("A2", "A", 1),
  page("A1x", "A1", 0),
];

describe("the zones of a row", () => {
  it("gives a quarter to each edge and the middle to 'onto'", () => {
    expect(dropZoneAt(1, 32)).toBe("before");
    expect(dropZoneAt(16, 32)).toBe("onto");
    expect(dropZoneAt(31, 32)).toBe("after");
    expect(dropZoneAt(5, 0)).toBe("onto");
  });

  it("maps a zone to a target", () => {
    expect(targetFor("onto", "B", null, 1)).toEqual({ kind: "onto", pageId: "B" });
    expect(targetFor("before", "B", null, 1)).toEqual({ kind: "between", parentId: null, index: 1 });
    expect(targetFor("after", "B", null, 1)).toEqual({ kind: "between", parentId: null, index: 2 });
  });

  it("reads the bottom edge of an open parent as its first child", () => {
    expect(targetFor("after", "A", null, 0, true)).toEqual({ kind: "between", parentId: "A", index: 0 });
    expect(targetFor("before", "A", null, 0, true)).toEqual({ kind: "between", parentId: null, index: 0 });
  });
});

describe("a reorder among the same siblings", () => {
  it("sends the place only", () => {
    // C to the gap before A.
    expect(planDrop(PAGES, "C", { kind: "between", parentId: null, index: 0 })).toEqual({
      move: { position: 0 },
    });
    // A to the gap after C: the place counts without A itself.
    expect(planDrop(PAGES, "A", { kind: "between", parentId: null, index: 3 })).toEqual({
      move: { position: 2 },
    });
    // A drop between two pages: C between A and B.
    expect(planDrop(PAGES, "C", { kind: "between", parentId: null, index: 1 })).toEqual({
      move: { position: 1 },
    });
  });

  it("asks nothing for a drop on either side of the page itself", () => {
    expect(planDrop(PAGES, "B", { kind: "between", parentId: null, index: 1 })).toEqual({ nothing: true });
    expect(planDrop(PAGES, "B", { kind: "between", parentId: null, index: 2 })).toEqual({ nothing: true });
  });

  it("asks nothing for a drop onto its own parent when it is already last", () => {
    expect(planDrop(PAGES, "A2", { kind: "onto", pageId: "A" })).toEqual({ nothing: true });
    expect(planDrop(PAGES, "A1", { kind: "onto", pageId: "A" })).toEqual({ move: { position: 1 } });
  });
});

describe("a nest", () => {
  it("puts the page last under the row it drops onto", () => {
    expect(planDrop(PAGES, "C", { kind: "onto", pageId: "A" })).toEqual({
      move: { parent_id: "A", position: 2 },
    });
    expect(planDrop(PAGES, "C", { kind: "onto", pageId: "B" })).toEqual({
      move: { parent_id: "B", position: 0 },
    });
  });

  it("puts the page at a gap among new siblings", () => {
    expect(planDrop(PAGES, "C", { kind: "between", parentId: "A", index: 1 })).toEqual({
      move: { parent_id: "A", position: 1 },
    });
  });

  it("takes a page out to the root with a null parent", () => {
    expect(planDrop(PAGES, "A1x", { kind: "between", parentId: null, index: 1 })).toEqual({
      move: { parent_id: null, position: 1 },
    });
  });
});

describe("a page cannot drop into its own subtree", () => {
  it("refuses the page itself and each page under it", () => {
    expect(planDrop(PAGES, "A", { kind: "onto", pageId: "A" })).toEqual({ refusal: "own-subtree" });
    expect(planDrop(PAGES, "A", { kind: "onto", pageId: "A1x" })).toEqual({ refusal: "own-subtree" });
    expect(planDrop(PAGES, "A", { kind: "between", parentId: "A1", index: 0 })).toEqual({
      refusal: "own-subtree",
    });
  });

  it("allows a child to go up past its parent", () => {
    expect(planDrop(PAGES, "A1", { kind: "onto", pageId: "B" })).toEqual({
      move: { parent_id: "B", position: 0 },
    });
  });

  it("knows the subtree", () => {
    expect([...subtreeIds(buildTree(PAGES), "A")].sort()).toEqual(["A", "A1", "A1x", "A2"]);
    expect([...subtreeIds(buildTree(PAGES), "nope")]).toEqual([]);
  });
});

describe("a page whose parent the member cannot see", () => {
  // `O` names a parent that is not in the list, so the tree draws it at the root.
  const withOrphan = [...PAGES, page("O", "hidden", 0, "O")];

  it("does not count as a sibling at the root", () => {
    const roots = buildTree(withOrphan).map((n) => n.page.id);
    expect(roots).toEqual(["A", "O", "B", "C"]);
    // C to the gap after O (between O and B): one real root sibling before it.
    expect(planDrop(withOrphan, "C", { kind: "between", parentId: null, index: 2 })).toEqual({
      move: { position: 1 },
    });
  });

  it("moves to the root for real when the member drops it there", () => {
    expect(planDrop(withOrphan, "O", { kind: "between", parentId: null, index: 0 })).toEqual({
      move: { parent_id: null, position: 0 },
    });
  });

  it("refuses an unknown page", () => {
    expect(planDrop(PAGES, "nope", { kind: "onto", pageId: "A" })).toEqual({ refusal: "unknown" });
  });
});

describe("the list after a drop, before the gateway answers", () => {
  const order = (pages: PageSummary[], parent: string | null) =>
    buildTree(pages)
      .flatMap(function flat(n): { id: string; parent: string | null }[] {
        return [{ id: n.page.id, parent: n.page.parent_id }, ...n.children.flatMap(flat)];
      })
      .filter((row) => row.parent === parent)
      .map((row) => row.id);

  it("draws a reorder at its place", () => {
    expect(order(applyMove(PAGES, "C", { position: 0 }), null)).toEqual(["C", "A", "B"]);
    expect(order(applyMove(PAGES, "A", { position: 2 }), null)).toEqual(["B", "C", "A"]);
  });

  it("draws a nest under its new parent", () => {
    const after = applyMove(PAGES, "C", { parent_id: "A", position: 1 });
    expect(order(after, "A")).toEqual(["A1", "C", "A2"]);
    expect(order(after, null)).toEqual(["A", "B"]);
  });
});
