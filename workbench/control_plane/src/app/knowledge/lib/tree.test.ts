/**
 * The page tree of one space (WS-61 KB-2a). Fence for `tree.ts`.
 *
 * The tree comes from `parent_id`, `position` and the title. A child whose
 * parent the member cannot see goes to the root and is never dropped.
 */
import { describe, expect, it } from "vitest";

import { ancestorIds, buildTree, type TreeNode } from "./tree";
import type { PageSummary } from "./types";

function page(id: string, over: Partial<PageSummary> = {}): PageSummary {
  return {
    id,
    space_id: "s1",
    parent_id: null,
    position: 0,
    title: id,
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
    created_at: "2026-10-01T00:00:00Z",
    ...over,
  };
}

/** The tree as nested ids, so a failure reads as a shape. */
function shape(nodes: TreeNode[]): unknown[] {
  return nodes.map((n) => (n.children.length ? { [n.page.id]: shape(n.children) } : n.page.id));
}

describe("buildTree", () => {
  it("nests a child under its parent", () => {
    const tree = buildTree([page("a"), page("b", { parent_id: "a" }), page("c", { parent_id: "b" })]);
    expect(shape(tree)).toEqual([{ a: [{ b: ["c"] }] }]);
  });

  it("orders siblings by position, then by the title with no case", () => {
    const tree = buildTree([
      page("x", { title: "zeta", position: 0 }),
      page("y", { title: "Alpha", position: 0 }),
      page("z", { title: "beta", position: 0 }),
      page("w", { title: "first", position: -1 }),
    ]);
    expect(shape(tree)).toEqual(["w", "y", "z", "x"]);
  });

  it("puts a child whose parent is invisible at the root, and keeps it", () => {
    const tree = buildTree([page("a"), page("orphan", { parent_id: "draft-of-someone-else" })]);
    expect(shape(tree)).toEqual(["a", "orphan"]);
  });

  it("keeps the subtree of an orphan with it", () => {
    const tree = buildTree([page("o", { parent_id: "gone" }), page("k", { parent_id: "o" })]);
    expect(shape(tree)).toEqual([{ o: ["k"] }]);
  });

  it("shows each page once when the parents make a loop", () => {
    const tree = buildTree([
      page("a", { parent_id: "b" }),
      page("b", { parent_id: "a" }),
      page("self", { parent_id: "self" }),
    ]);
    const ids: string[] = [];
    const walk = (nodes: TreeNode[]) => nodes.forEach((n) => (ids.push(n.page.id), walk(n.children)));
    walk(tree);
    expect(ids.sort()).toEqual(["a", "b", "self"]);
  });

  it("is empty for no pages", () => {
    expect(buildTree([])).toEqual([]);
  });
});

describe("ancestorIds", () => {
  const pages = [page("a"), page("b", { parent_id: "a" }), page("c", { parent_id: "b" })];

  it("names the pages above, nearest first", () => {
    expect(ancestorIds(pages, "c")).toEqual(["b", "a"]);
  });

  it("is empty for a root, an unknown page and no page", () => {
    expect(ancestorIds(pages, "a")).toEqual([]);
    expect(ancestorIds(pages, "nope")).toEqual([]);
    expect(ancestorIds(pages, null)).toEqual([]);
  });

  it("stops at a loop", () => {
    expect(ancestorIds([page("a", { parent_id: "b" }), page("b", { parent_id: "a" })], "a")).toEqual(["b"]);
  });
});
